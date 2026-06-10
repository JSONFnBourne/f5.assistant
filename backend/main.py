import asyncio
import ctypes
import ctypes.util
import gc
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

logger = logging.getLogger("f5_backend")

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB


# Analyzing a 773 MB VELOS partition archive peaks around 7.4 GB resident.
# CPython's arena allocator + glibc don't return that to the OS on their own,
# so after a handful of requests the uvicorn worker looks pinned at multi-GB
# RSS even though nothing is live. malloc_trim(0) pushes freed glibc arenas
# back; on non-glibc platforms the helper is a no-op.
_LIBC_MALLOC_TRIM = None


def _load_malloc_trim():
    global _LIBC_MALLOC_TRIM
    if _LIBC_MALLOC_TRIM is not None:
        return _LIBC_MALLOC_TRIM
    libc_path = ctypes.util.find_library("c")
    if not libc_path:
        _LIBC_MALLOC_TRIM = False
        return False
    try:
        libc = ctypes.CDLL(libc_path)
    except OSError:
        _LIBC_MALLOC_TRIM = False
        return False
    if not hasattr(libc, "malloc_trim"):
        _LIBC_MALLOC_TRIM = False
        return False
    libc.malloc_trim.argtypes = [ctypes.c_size_t]
    libc.malloc_trim.restype = ctypes.c_int
    _LIBC_MALLOC_TRIM = libc.malloc_trim
    return _LIBC_MALLOC_TRIM


def _reclaim_memory():
    """gc.collect() + glibc malloc_trim(0). Called at the end of every analyze
    request so the long-lived worker doesn't retain analysis-peak RSS."""
    gc.collect()
    trim = _load_malloc_trim()
    if trim:
        try:
            trim(0)
        except OSError:
            pass


from qkview_analyzer.config_parser import (
    BigIPConfig,
    parse_bigip_base_conf,
    parse_bigip_conf,
)
from qkview_analyzer.extractor import extract_qkview
from qkview_analyzer.indexer import LogIndexer
from qkview_analyzer.parser import parse_all_logs, parse_f5os_event_log
from qkview_analyzer.reporter import Reporter
from qkview_analyzer.rule_engine import Finding, RuleEngine
from qkview_analyzer.tmos_config import (
    app_details,
    app_summary,
    list_partitions,
    parse_tmos_config,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hook. Replaces the deprecated @app.on_event handler."""
    init_db()
    yield


app = FastAPI(title="F5 Assistant Backend API", version="0.7.0", lifespan=lifespan)

_ALLOWED_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN],
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Filename"],
)

DB_PATH = os.path.join(os.path.dirname(__file__), "f5_assistant.db")

# Per-analysis on-disk log indexes (SQLite FTS5). The index built during
# analysis (and used by the rule engine) is persisted here as
# <analysis_id>.db so GET /api/qkview/{id}/logs can search it later.
LOG_INDEX_DIR = os.path.join(os.path.dirname(__file__), "data", "log_index")


def _log_index_path(analysis_id: int) -> str:
    return os.path.join(LOG_INDEX_DIR, f"{analysis_id}.db")


def _sweep_log_indexes(conn: sqlite3.Connection) -> None:
    """Delete log-index DB files with no surviving `analyses` row.

    Runs after the 30-day retention DELETE so indexes belonging to swept
    rows — and any orphans from interrupted runs — are removed together.
    Temp files (non-numeric stems) are left alone: a concurrent analysis
    may still be writing one.
    """
    if not os.path.isdir(LOG_INDEX_DIR):
        return
    keep = {row[0] for row in conn.execute("SELECT id FROM analyses")}
    for name in os.listdir(LOG_INDEX_DIR):
        stem, ext = os.path.splitext(name)
        if ext != ".db" or not stem.isdigit():
            continue
        if int(stem) not in keep:
            try:
                os.remove(os.path.join(LOG_INDEX_DIR, name))
            except OSError:
                pass


def init_db():
    """Initialize the SQLite database with required schemas."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Create tables for QKView analysis summaries and Chat History
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            analysis_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary JSON NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


@app.get("/")
async def root():
    return {"message": "F5 Assistant Backend API is running."}


@app.get("/health")
async def health_check():
    return {"status": "ok", "db_initialized": os.path.exists(DB_PATH)}


@app.options("/api/analyze")
async def analyze_qkview_options():
    return {}


@app.post("/api/analyze")
async def analyze_qkview(request: Request):
    """Upload a qkview file (raw octet-stream), analyze it, save to DB, stream NDJSON progress.

    Accepts the archive body directly — no multipart. Filename is supplied via
    the X-Filename header. Skipping multipart avoids the pure-Python,
    GIL-bound parser in python-multipart which throttles large uploads to
    under 1 MB/s on this host.

    Response is application/x-ndjson. Each line is a JSON object:
      {"type":"progress","msg":"..."}      — status update from a pipeline stage
      {"type":"result","status":"success","filename":"...","data":{...}}  — final payload
      {"type":"error","status_code":4xx|500,"detail":"..."}                — failure
    """
    filename = request.headers.get("x-filename") or "upload.qkview"
    allowed_extensions = (".qkview", ".tgz", ".tar.gz", ".tar")
    if not filename.endswith(allowed_extensions):
        raise HTTPException(
            status_code=400, detail=f"File must be an archive of types: {allowed_extensions}"
        )

    temp_path = None
    bytes_written = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".qkview") as temp_file:
            temp_path = temp_file.name
            async for chunk in request.stream():
                if not chunk:
                    continue
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds 1 GB limit.")
                temp_file.write(chunk)
    except BaseException:
        # Spooling failed (client disconnect, size cap, …) — never leak the
        # delete=False temp file into /tmp (a 16 GB tmpfs on this host).
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    if bytes_written == 0:
        os.remove(temp_path)
        raise HTTPException(status_code=400, detail="Empty upload body.")

    # Validate file content before starting the streaming pipeline — that way
    # bad uploads still return a plain 4xx instead of a 200 with a stream.
    import tarfile as _tarfile

    if filename.endswith(".tar"):
        if not _tarfile.is_tarfile(temp_path):
            os.remove(temp_path)
            raise HTTPException(
                status_code=400, detail="Invalid file content: not a valid tar archive."
            )
    else:
        with open(temp_path, "rb") as f:
            magic = f.read(2)
        if magic != b"\x1f\x8b":
            os.remove(temp_path)
            raise HTTPException(
                status_code=400, detail="Invalid file content: not a valid gzip/qkview archive."
            )

    loop = asyncio.get_running_loop()
    # Unbounded: put_nowait runs inside loop callbacks where QueueFull would
    # be swallowed — a dropped result/sentinel hangs the stream forever.
    queue: asyncio.Queue = asyncio.Queue()

    def push(event: dict) -> None:
        """Thread-safe emit of one NDJSON event to the streaming response."""
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def progress(msg: str) -> None:
        push({"type": "progress", "msg": msg})

    def worker() -> None:
        indexer: LogIndexer | None = None
        # The index is built at a temp path inside LOG_INDEX_DIR (same
        # filesystem as its final name) because the analysis_id it will be
        # named after only exists once the summary row is INSERTed. Set to
        # None after the promoting rename; the finally block removes any
        # leftover temp file on failure paths.
        temp_index_path: str | None = None
        try:
            progress("Extracting archive…")
            data = extract_qkview(temp_path, progress_callback=progress)

            progress("Parsing log files…")
            entries = parse_all_logs(data.log_files, progress_callback=progress)

            is_f5os = data.meta.product == "F5OS"
            if is_f5os:
                if data.f5os_event_log:
                    event_entries = parse_f5os_event_log(
                        data.f5os_event_log, source_file="event-log.log"
                    )
                    entries.extend(event_entries)
                if data.f5os_system_events:
                    sys_event_entries = parse_f5os_event_log(
                        data.f5os_system_events, source_file="system-events"
                    )
                    entries.extend(sys_event_entries)
                entries.sort(key=lambda e: e.timestamp)

            progress(f"Indexing {len(entries)} log entries…")
            os.makedirs(LOG_INDEX_DIR, exist_ok=True)
            temp_index_path = os.path.join(LOG_INDEX_DIR, f"tmp_{uuid.uuid4().hex}.db")
            indexer = LogIndexer(db_path=temp_index_path)
            indexer.bulk_insert(entries, progress_callback=progress)
            # Hostname fallback source: most common hostname across parsed
            # entries. Must be computed before `entries` is dropped below.
            hostname_counts = Counter(
                e.hostname
                for e in entries
                if e.hostname and e.hostname not in ("", "-", "localhost")
            )
            fallback_hostname = hostname_counts.most_common(1)[0][0] if hostname_counts else None
            # `entries` is duplicated into the SQLite index; drop our copy now
            # so gc can reclaim it before the Reporter/json stage allocates.
            entries = []

            config = BigIPConfig()
            tmos_tree: dict = {}
            if not is_f5os:
                progress("Parsing bigip.conf / bigip_base.conf…")
                if "config/bigip.conf" in data.config_files:
                    config = parse_bigip_conf(data.config_files["config/bigip.conf"])
                if "config/bigip_base.conf" in data.config_files:
                    base_config = parse_bigip_base_conf(data.config_files["config/bigip_base.conf"])
                    config.vlans = base_config.vlans
                    config.self_ips = base_config.self_ips
                    if base_config.hostname and base_config.hostname.lower() != "localhost":
                        data.meta.hostname = base_config.hostname

                progress("Building universal TMOS config tree…")
                try:
                    # Root bigip.conf carries /Common objects; per-partition
                    # dumps under config/partitions/<name>/bigip.conf carry
                    # their partition's objects (/DMZ, /public, ...). Merge
                    # them all so VS from every partition land in tmos_tree.
                    root_names = (
                        "config/bigip.conf",
                        "config/bigip_base.conf",
                        "config/bigip_gtm.conf",
                    )
                    partition_names = sorted(
                        name
                        for name in data.config_files.keys()
                        if name.startswith("config/partitions/") and name.endswith((".conf",))
                    )
                    combined = "\n".join(
                        data.config_files.get(name, "") for name in (*root_names, *partition_names)
                    )
                    if combined.strip():
                        tmos_tree = parse_tmos_config(combined)
                except Exception:
                    logger.exception("Universal TMOS parser failed; continuing without app tree")
                    tmos_tree = {}

            # Hostname fallback: config didn't provide one — use the most
            # common hostname seen in the parsed log entries.
            if not data.meta.hostname and fallback_hostname:
                data.meta.hostname = fallback_hostname

            progress("Running rule engine scan…")
            engine = RuleEngine(platform="f5os" if is_f5os else "tmos")
            findings = engine.scan(indexer, progress_callback=progress)

            if is_f5os and data.f5os_health:
                for h in data.f5os_health:
                    if h.health == "unhealthy":
                        findings.append(
                            Finding(
                                rule_name=f"f5os-health-{h.component}",
                                rule_description=f"F5OS Health: {h.component} — {h.description}",
                                severity="critical" if h.severity == "critical" else "warning",
                                category="hardware",
                                recommendation=f"Check {h.component} hardware status. Component reports: {h.description}",
                                count=1,
                            )
                        )

            progress("Generating summary…")
            queried = indexer.query(min_severity="warning", limit=5000)
            summary_dict = json.loads(
                Reporter.to_json(
                    data.meta,
                    queried,
                    findings,
                    config if not is_f5os else None,
                    qkview_data=data,
                )
            )

            if tmos_tree:
                summary_dict["tmos_config"] = tmos_tree
                summary_dict["partitions"] = list_partitions(tmos_tree)
                summary_dict["apps"] = app_summary(tmos_tree)

            # Trim log entries BEFORE persisting and streaming. VELOS
            # partition findings have been observed with 88 MB multi-line
            # "messages" that blow the client stream past 500 MB — and
            # storing them untrimmed bloats the analyses table the same way.
            # config/apps/partitions/tmos_config stay full fidelity: the
            # GET /api/qkview/{id}/apps endpoints serve from the stored copy.
            MAX_MESSAGE_BYTES = 2048

            def _trim_entry(e):
                # webapp/app/qkview/page.tsx renders sample.raw_line and
                # entry.raw_line; `message` is a duplicate we drop. Truncate
                # raw_line to keep the UI responsive when the log parser
                # produces a single 88 MB "entry".
                if not isinstance(e, dict):
                    return e
                out = {k: v for k, v in e.items() if k != "message"}
                raw = out.get("raw_line")
                if isinstance(raw, str) and len(raw) > MAX_MESSAGE_BYTES:
                    out["raw_line"] = (
                        raw[:MAX_MESSAGE_BYTES]
                        + f"\n…[truncated, {len(raw) - MAX_MESSAGE_BYTES} more bytes]"
                    )
                return out

            if isinstance(summary_dict.get("entries"), list):
                summary_dict["entries"] = [_trim_entry(e) for e in summary_dict["entries"][:300]]
            if isinstance(summary_dict.get("findings"), list):
                summary_dict["findings"] = [
                    (
                        {
                            **f,
                            "sample_entries": [_trim_entry(s) for s in f.get("sample_entries", [])],
                        }
                        if isinstance(f, dict)
                        else f
                    )
                    for f in summary_dict["findings"]
                ]

            # sqlite3.Connection's context manager commits but does not close
            # the connection — explicit close keeps per-request connections
            # from accumulating in the long-lived worker.
            conn = sqlite3.connect(DB_PATH)
            try:
                # Retention sweep: stored summaries older than 30 days are
                # dead weight — drop them before adding the new row.
                conn.execute(
                    "DELETE FROM analyses WHERE analysis_date < datetime('now','-30 days')"
                )
                cursor = conn.execute(
                    "INSERT INTO analyses (filename, summary) VALUES (?, ?)",
                    (filename, json.dumps(summary_dict, default=str)),
                )
                analysis_id = cursor.lastrowid
                conn.commit()
                # Remove log-index files orphaned by the retention DELETE
                # above (or by earlier interrupted runs).
                _sweep_log_indexes(conn)
            finally:
                conn.close()

            # Promote the on-disk index to its permanent per-analysis name
            # now that the id exists. The indexer is done being queried at
            # this point (rule engine + summary query ran above); close it
            # so the SQLite file is complete before the rename.
            indexer.close()
            os.replace(temp_index_path, _log_index_path(analysis_id))
            temp_index_path = None

            # Client stream drops tmos_config (megabytes the browser never
            # renders); the /apps endpoints read it from the stored summary.
            client_dict = {k: v for k, v in summary_dict.items() if k != "tmos_config"}
            client_dict["analysis_id"] = analysis_id

            push(
                {
                    "type": "result",
                    "status": "success",
                    "filename": filename,
                    "data": client_dict,
                }
            )
        except Exception:
            import traceback

            logger.error("FAILED TO ANALYZE QKVIEW:\n%s", traceback.format_exc())
            push(
                {
                    "type": "error",
                    "status_code": 500,
                    "detail": "Analysis failed. Check server logs for details.",
                }
            )
        finally:
            # Close the index SQLite even on error paths — otherwise a
            # failed analyze leaves the connection (and its page cache)
            # resident until the worker thread's frame is GC'd. close()
            # after close() is a no-op, so the success path is unaffected.
            if indexer is not None:
                try:
                    indexer.close()
                except Exception:
                    pass
            # Analysis failed before the index was promoted — don't leave
            # the temp index file behind in LOG_INDEX_DIR.
            if temp_index_path and os.path.exists(temp_index_path):
                try:
                    os.remove(temp_index_path)
                except OSError:
                    pass
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            _reclaim_memory()
            push(None)  # sentinel: close the stream

    threading.Thread(target=worker, daemon=True).start()

    async def ndjson_stream():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield (json.dumps(item) + "\n").encode("utf-8")

    return StreamingResponse(
        ndjson_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _load_summary(analysis_id: int) -> dict:
    """Fetch a stored analysis summary JSON blob from SQLite."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT summary FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Corrupt analysis summary") from exc


@app.get("/api/qkview/{analysis_id}/apps")
def list_qkview_apps(analysis_id: int, partition: str | None = None):
    # Plain `def`: FastAPI runs it on the threadpool, so the sync SQLite
    # read + json.loads in _load_summary can't block the event loop.
    """Return the list of virtual-server app summaries for a stored analysis.

    Optional `?partition=Common` filters to a single partition.
    """
    summary = _load_summary(analysis_id)
    tree = summary.get("tmos_config")
    if not tree:
        return {"analysis_id": analysis_id, "apps": [], "partitions": []}
    return {
        "analysis_id": analysis_id,
        "partitions": list_partitions(tree),
        "apps": app_summary(tree, partition=partition),
    }


@app.get("/api/qkview/{analysis_id}/apps/{full_path:path}")
def qkview_app_details(analysis_id: int, full_path: str):
    # Plain `def` — threadpooled for the same reason as list_qkview_apps.
    """Return the consolidated stanza set for a single virtual server."""
    if not full_path.startswith("/"):
        full_path = "/" + full_path
    summary = _load_summary(analysis_id)
    tree = summary.get("tmos_config")
    if not tree:
        raise HTTPException(status_code=404, detail="No TMOS config tree stored")
    details = app_details(tree, full_path)
    if details is None:
        raise HTTPException(status_code=404, detail=f"App not found: {full_path}")
    return {"analysis_id": analysis_id, "app": details}


# FTS5 treats many characters as operators ('-' is NOT, '"' quotes, etc.).
# Mirror the webapp knowledge-retrieval sanitizer: strip to word chars,
# emit each surviving term as a quoted token (implicit AND between them).
# User text NEVER reaches MATCH unquoted.
def _build_fts_match(q: str) -> str | None:
    sanitized = re.sub(r"[^\w\s]", " ", q)[:512]
    terms = [t for t in sanitized.split() if len(t) >= 2]
    if not terms:
        return None
    return " ".join(f'"{t}"' for t in terms)


def _parse_iso_param(name: str, value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid `{name}` timestamp: {value!r} (expected ISO 8601)",
        ) from exc


@app.get("/api/qkview/{analysis_id}/logs")
def search_qkview_logs(
    analysis_id: int,
    q: str | None = None,
    severity: str | None = None,
    process: str | None = None,
    source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
    facets: bool = False,
):
    # Plain `def` — threadpooled like the /apps endpoints, so the sync
    # SQLite reads can't block the event loop.
    """Search the persisted per-analysis log index.

    Query params: `q` (full-text, sanitized), `severity`/`process`/`source`
    (exact filters), `since`/`until` (ISO timestamps), `limit` (default 50,
    max 500), `offset`, `facets` (include distinct severity/process/source
    counts for filter dropdowns).
    """
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT 1 FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    index_path = _log_index_path(analysis_id)
    if not os.path.exists(index_path):
        raise HTTPException(
            status_code=404,
            detail=(
                "No log index stored for this analysis — it predates the "
                "log-search feature. Re-run the analysis to generate one."
            ),
        )

    conditions: list[str] = []
    params: list = []
    if severity:
        conditions.append("logs.severity = ?")
        params.append(severity.lower())
    if process:
        conditions.append("logs.process = ?")
        params.append(process)
    if source:
        conditions.append("logs.source_file = ?")
        params.append(source)
    if since:
        conditions.append("logs.timestamp_epoch >= ?")
        params.append(_parse_iso_param("since", since))
    if until:
        conditions.append("logs.timestamp_epoch <= ?")
        params.append(_parse_iso_param("until", until))

    match_expr = _build_fts_match(q) if q else None
    where = " AND ".join(conditions) if conditions else "1=1"
    if match_expr:
        base = (
            "FROM logs JOIN logs_fts ON logs.id = logs_fts.rowid "
            f"WHERE logs_fts MATCH ? AND {where}"
        )
        params = [match_expr, *params]
    else:
        base = f"FROM logs WHERE {where}"

    # Read-only URI open: the endpoint must never create or mutate index files.
    con = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        total = con.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        rows = con.execute(
            "SELECT logs.timestamp, logs.hostname, logs.process, logs.severity, "
            f"logs.msg_code, logs.source_file, logs.message, logs.raw_line {base} "
            "ORDER BY logs.timestamp_epoch ASC, logs.id ASC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        entries = [
            {
                "timestamp": r["timestamp"],
                "host": r["hostname"],
                "process": r["process"],
                "severity": r["severity"],
                "msg_code": r["msg_code"],
                "source": r["source_file"],
                "message": r["message"],
                "raw_line": r["raw_line"],
            }
            for r in rows
        ]
        result = {
            "analysis_id": analysis_id,
            "total": total,
            "limit": limit,
            "offset": offset,
            "entries": entries,
            "capped": offset + len(entries) < total,
        }
        if facets:
            result["facets"] = {
                "severities": [
                    {"value": r[0], "count": r[1]}
                    for r in con.execute(
                        "SELECT severity, COUNT(*) FROM logs "
                        "GROUP BY severity ORDER BY MIN(severity_num)"
                    )
                ],
                "processes": [
                    {"value": r[0], "count": r[1]}
                    for r in con.execute(
                        "SELECT process, COUNT(*) FROM logs WHERE process IS NOT NULL "
                        "GROUP BY process ORDER BY COUNT(*) DESC LIMIT 100"
                    )
                ],
                "sources": [
                    {"value": r[0], "count": r[1]}
                    for r in con.execute(
                        "SELECT source_file, COUNT(*) FROM logs "
                        "GROUP BY source_file ORDER BY COUNT(*) DESC LIMIT 100"
                    )
                ],
            }
        return result
    finally:
        con.close()


if __name__ == "__main__":
    import uvicorn

    # Start the application on port 8000
    uvicorn.run("main:app", host="127.0.0.1", port=8000)
