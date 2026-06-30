#!/usr/bin/env python3
"""ingest_bugtracker.py — ingest scraped F5 bug tracker pages into knowledge.db
as source `bugtracker`.

Source: data/bugtracker/pages/ID######.html (fetched from
https://cdn.f5.com/product/bugtracker/ID######.html — see fetch_all.sh).

One document per bug (doc_id = "ID######"). Extracts the human-readable fields
F5 publishes: title, Severity, Affected Versions, Fixed In, Opened, Symptoms,
Conditions, Impact, Workaround, Fix Information, Behavior Change, and related
K-article links. Raw HTML is NOT stored — only the assembled plain text, so it
FTS-indexes cleanly. GPU-free (FTS5/BM25). Run from repo root:

  .venv/bin/python scripts/ingest_bugtracker.py --dry-run        # preview
  .venv/bin/python scripts/ingest_bugtracker.py [--limit N]      # ingest
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from utils.db import upsert_document

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "db" / "knowledge.db"
PAGES = REPO / "data" / "bugtracker" / "pages"
_FETCH_LOG_PATH = REPO / "data" / "bugtracker" / "fetch_log.jsonl"


def _load_fetch_log() -> dict[str, str]:
    """Map ID -> scrape date (YYYY-MM-DD) from the durable fetch log, if present."""
    out: dict[str, str] = {}
    if _FETCH_LOG_PATH.exists():
        for line in _FETCH_LOG_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                out[e["id"]] = e["ts"][:10]
            except Exception:  # noqa: BLE001
                pass
    return out


_FETCH_LOG = _load_fetch_log()

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# <h4>Label</h4> <p>body…</p>  — the narrative sections
_SECTION = re.compile(r"<h4>\s*([^<]+?)\s*</h4>\s*<p>(.*?)</p>", re.S | re.I)
# inline "Label: </span>value" or "Label:</span><br/> <span>value</span>"
_META = {
    "severity": re.compile(r"Severity:\s*</span>\s*([^<]+)", re.I),
    "affected_versions": re.compile(r"Affected Versions?:\s*</span>\s*(?:<br/?>)?\s*<span>(.*?)</span>", re.S | re.I),
    "fixed_in": re.compile(r"Fixed In:\s*</span>\s*(?:<br/?>)?\s*<span>(.*?)</span>", re.S | re.I),
    "opened": re.compile(r"Opened:\s*</span>\s*([^<]+)", re.I),
    "component": re.compile(r"Component:\s*</span>\s*([^<]+)", re.I),
}
_META_TITLE = re.compile(r'<meta name="title"\s+content="([^"]*)"', re.I)
_KLINK = re.compile(r"(K\d{4,}|K0000\d+)", re.I)


def _text(s: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", s or ""))).strip()


def parse(path: Path) -> dict | None:
    h = path.read_text(encoding="utf-8", errors="replace")
    if len(h) < 1000:
        return None
    num = path.stem.replace("ID", "")
    # Scrape-time provenance: prefer the durable fetch_log (written by
    # fetch_all.sh); fall back to the file mtime. Stamped into the doc so the
    # KB/LLM know how current the bug data is, independent of ingest time.
    scraped = _FETCH_LOG.get(f"ID{num}")
    if not scraped:
        scraped = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
    rec = {"bug_id": num, "scraped_at": scraped}
    mt = _META_TITLE.search(h)
    rec["title"] = _text(mt.group(1)) if mt else f"F5 bug {num}"
    for key, rx in _META.items():
        m = rx.search(h)
        if m:
            rec[key] = _text(m.group(1))
    sections = {}
    for label, body in _SECTION.findall(h):
        lab = _text(label).lower().replace(" ", "_")
        val = _text(body)
        if val and val.lower() != "none":
            sections[lab] = val
    rec["sections"] = sections
    # related K-articles (in the Guides & references block)
    refs = sorted({k.upper() for k in _KLINK.findall(h)})
    rec["k_refs"] = refs
    return rec


def to_content(rec: dict) -> str:
    parts = [f"F5 Bug ID {rec['bug_id']}: {rec['title']}"]
    meta_bits = []
    for k in ("severity", "component", "opened", "fixed_in", "affected_versions"):
        if rec.get(k):
            meta_bits.append(f"{k.replace('_', ' ').title()}: {rec[k]}")
    if meta_bits:
        parts.append(" | ".join(meta_bits))
    for sec in ("symptoms", "conditions", "impact", "workaround", "fix_information", "behavior_change"):
        if rec["sections"].get(sec):
            parts.append(f"{sec.replace('_', ' ').title()}: {rec['sections'][sec]}")
    if rec["k_refs"]:
        parts.append("Related articles: " + ", ".join(rec["k_refs"]))
    parts.append(f"Data scraped: {rec['scraped_at']}")
    return "\n\n".join(parts)


def build_record(rec: dict) -> dict:
    num = rec["bug_id"]
    content = to_content(rec)
    # both "ID######" and bare number in keywords so either query form hits FTS
    kw = [f"ID{num}", num, "F5 bug", "bugtracker", f"scraped:{rec['scraped_at']}"]
    if rec.get("severity"):
        kw.append(rec["severity"])
    if rec.get("component"):
        kw.append(rec["component"])
    return dict(
        source="bugtracker",
        doc_id=f"ID{num}",
        title=f"Bug ID {num}: {rec['title']}",
        url=f"https://cdn.f5.com/product/bugtracker/ID{num}.html",
        section=rec.get("component") or rec.get("severity"),
        keywords=json.dumps(kw),
        content=content,
        local_path=str((PAGES / f"ID{num}.html").relative_to(REPO)),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()

    files = sorted(PAGES.glob("ID*.html"))
    if args.limit:
        files = files[: args.limit]
    print(f"found {len(files)} fetched pages")

    parsed, skipped = [], 0
    for f in files:
        rec = parse(f)
        # accept any real bug page (has narrative sections OR a Severity field);
        # this excludes 404 pages, which carry neither.
        if rec and (rec["sections"] or rec.get("severity")):
            parsed.append(rec)
        else:
            skipped += 1
    print(f"parsed {len(parsed)} bugs with content; skipped {skipped} (empty/unparseable)")

    if args.dry_run:
        lens = [len(to_content(r)) for r in parsed] or [0]
        nrefs = sum(1 for r in parsed if r["k_refs"])
        print(f"content len: min={min(lens)} median={sorted(lens)[len(lens)//2]} max={max(lens)}")
        print(f"bugs with related K-articles: {nrefs}/{len(parsed)}")
        for r in parsed[: args.samples]:
            rec = build_record(r)
            print(f"\n[doc_id] {rec['doc_id']}  [sev] {r.get('severity')}  [fixed] {r.get('fixed_in')}")
            print(f"[title] {rec['title']}")
            print(f"[content {len(rec['content'])} chars]\n{rec['content'][:600]}…")
        return

    for r in parsed:
        upsert_document(DB, **build_record(r))
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM documents WHERE source='bugtracker'").fetchone()[0]
    print(f"ingested; documents WHERE source='bugtracker' = {n}; FTS rebuilt")
    # No dense-index refresh here: source `bugtracker` is BM25 direct-lookup only
    # and is excluded from the embedding index (see scripts/build_embeddings.py).


if __name__ == "__main__":
    main()
