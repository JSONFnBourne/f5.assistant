from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import asyncio
import ctypes
import ctypes.util
import gc
import sqlite3
import os
import tempfile
import threading
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

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

from qkview_analyzer.extractor import extract_qkview
from qkview_analyzer.parser import parse_all_logs, parse_f5os_event_log
from qkview_analyzer.indexer import LogIndexer
from qkview_analyzer.config_parser import parse_bigip_conf, parse_bigip_base_conf, BigIPConfig
from qkview_analyzer.rule_engine import RuleEngine, Finding
from qkview_analyzer.reporter import Reporter
from qkview_analyzer.tmos_config import (
    parse_tmos_config,
    list_partitions,
    app_summary,
    app_details,
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

def init_db():
    """Initialize the SQLite database with required schemas."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Create tables for QKView analysis summaries and Chat History
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            analysis_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary JSON NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
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
        raise HTTPException(status_code=400, detail=f"File must be an archive of types: {allowed_extensions}")

    temp_path = None
    with tempfile.NamedTemporaryFile(delete=False, suffix=".qkview") as temp_file:
        bytes_written = 0
        async for chunk in request.stream():
            if not chunk:
                continue
            bytes_written += len(chunk)
            if bytes_written > MAX_UPLOAD_BYTES:
                temp_file.close()
                os.remove(temp_file.name)
                raise HTTPException(status_code=413, detail="File exceeds 1 GB limit.")
            temp_file.write(chunk)
        temp_path = temp_file.name

    if bytes_written == 0:
        os.remove(temp_path)
        raise HTTPException(status_code=400, detail="Empty upload body.")

    # Validate file content before starting the streaming pipeline — that way
    # bad uploads still return a plain 4xx instead of a 200 with a stream.
    import tarfile as _tarfile
    if filename.endswith(".tar"):
        if not _tarfile.is_tarfile(temp_path):
            os.remove(temp_path)
            raise HTTPException(status_code=400, detail="Invalid file content: not a valid tar archive.")
    else:
        with open(temp_path, "rb") as f:
            magic = f.read(2)
        if magic != b'\x1f\x8b':
            os.remove(temp_path)
            raise HTTPException(status_code=400, detail="Invalid file content: not a valid gzip/qkview archive.")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    def push(event: dict) -> None:
        """Thread-safe emit of one NDJSON event to the streaming response."""
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def progress(msg: str) -> None:
        push({"type": "progress", "msg": msg})

    def worker() -> None:
        indexer: Optional[LogIndexer] = None
        try:
            progress("Extracting archive…")
            data = extract_qkview(temp_path, progress_callback=progress)

            progress("Parsing log files…")
            entries = parse_all_logs(data.log_files, progress_callback=progress)

            is_f5os = data.meta.product == "F5OS"
            if is_f5os:
                if data.f5os_event_log:
                    event_entries = parse_f5os_event_log(data.f5os_event_log, source_file="event-log.log")
                    entries.extend(event_entries)
                if data.f5os_system_events:
                    sys_event_entries = parse_f5os_event_log(data.f5os_system_events, source_file="system-events")
                    entries.extend(sys_event_entries)
                entries.sort(key=lambda e: e.timestamp)

            progress(f"Indexing {len(entries)} log entries…")
            indexer = LogIndexer()
            indexer.bulk_insert(entries, progress_callback=progress)
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
                        if name.startswith("config/partitions/")
                        and name.endswith((".conf",))
                    )
                    combined = "\n".join(
                        data.config_files.get(name, "")
                        for name in (*root_names, *partition_names)
                    )
                    if combined.strip():
                        tmos_tree = parse_tmos_config(combined)
                except Exception:
                    logger.exception("Universal TMOS parser failed; continuing without app tree")
                    tmos_tree = {}

            # Hostname fallback: derive from most common hostname in parsed log entries
            if not data.meta.hostname and entries:
                from collections import Counter
                hostnames = [e.hostname for e in entries if e.hostname and e.hostname not in ("", "-", "localhost")]
                if hostnames:
                    data.meta.hostname = Counter(hostnames).most_common(1)[0][0]

            progress("Running rule engine scan…")
            engine = RuleEngine(platform="f5os" if is_f5os else "tmos")
            findings = engine.scan(indexer, progress_callback=progress)

            if is_f5os and data.f5os_health:
                for h in data.f5os_health:
                    if h.health == "unhealthy":
                        findings.append(Finding(
                            rule_name=f"f5os-health-{h.component}",
                            rule_description=f"F5OS Health: {h.component} — {h.description}",
                            severity="critical" if h.severity == "critical" else "warning",
                            category="hardware",
                            recommendation=f"Check {h.component} hardware status. Component reports: {h.description}",
                            count=1,
                        ))

            progress("Generating summary…")
            queried = indexer.query(min_severity="warning", limit=5000)
            json_str = Reporter.to_json(
                data.meta,
                queried,
                findings,
                config if not is_f5os else None,
                qkview_data=data,
            )
            summary_dict = json.loads(json_str)

            if tmos_tree:
                summary_dict["tmos_config"] = tmos_tree
                summary_dict["partitions"] = list_partitions(tmos_tree)
                summary_dict["apps"] = app_summary(tmos_tree)
                json_str = json.dumps(summary_dict, default=str)

            # sqlite3.Connection's context manager commits but does not close
            # the connection — explicit close keeps per-request connections
            # from accumulating in the long-lived worker.
            conn = sqlite3.connect(DB_PATH)
            try:
                cursor = conn.execute(
                    "INSERT INTO analyses (filename, summary) VALUES (?, ?)",
                    (filename, json_str)
                )
                analysis_id = cursor.lastrowid
                conn.commit()
            finally:
                conn.close()

            # Trim the streamed payload so the browser doesn't freeze parsing
            # and rendering megabytes it will never show. SQLite keeps the full
            # summary (including tmos_config) for the /apps detail endpoints.
            # VELOS partition findings have been observed with 88 MB multi-line
            # "messages" that blow the client stream past 500 MB.
            MAX_MESSAGE_BYTES = 2048

            def _trim_entry(e):
                # webapp/app/qkview/page.tsx renders sample.raw_line and
                # entry.raw_line; `message` is a duplicate we drop for the
                # client stream. Truncate raw_line to keep the UI responsive
                # when the log parser produces a single 88 MB "entry".
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

            client_dict = {k: v for k, v in summary_dict.items() if k != "tmos_config"}
            client_dict["analysis_id"] = analysis_id
            if isinstance(client_dict.get("entries"), list):
                client_dict["entries"] = [_trim_entry(e) for e in client_dict["entries"][:300]]
            if isinstance(client_dict.get("findings"), list):
                client_dict["findings"] = [
                    {**f, "sample_entries": [_trim_entry(s) for s in f.get("sample_entries", [])]}
                    if isinstance(f, dict) else f
                    for f in client_dict["findings"]
                ]

            push({
                "type": "result",
                "status": "success",
                "filename": filename,
                "data": client_dict,
            })
        except Exception:
            import traceback
            logger.error("FAILED TO ANALYZE QKVIEW:\n%s", traceback.format_exc())
            push({
                "type": "error",
                "status_code": 500,
                "detail": "Analysis failed. Check server logs for details.",
            })
        finally:
            # Close the :memory: SQLite even on error paths — otherwise a
            # failed analyze leaves the entire indexed log set resident
            # until the worker thread's frame is GC'd.
            if indexer is not None:
                try:
                    indexer.close()
                except Exception:
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
        row = conn.execute(
            "SELECT summary FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Corrupt analysis summary") from exc


@app.get("/api/qkview/{analysis_id}/apps")
async def list_qkview_apps(analysis_id: int, partition: Optional[str] = None):
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
async def qkview_app_details(analysis_id: int, full_path: str):
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


if __name__ == "__main__":
    import uvicorn
    # Start the application on port 8000
    uvicorn.run("main:app", host="127.0.0.1", port=8000)
