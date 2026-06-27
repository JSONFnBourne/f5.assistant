#!/usr/bin/env python3
"""ingest_f5os_api.py — ingest F5OS-A (rSeries) + F5OS-C (VELOS) Swagger/OpenAPI
JSON schemas into knowledge.db as source `f5os_api`.

Net-new F5OS API coverage (Session-24 P4). One document per JSON module (~260
docs), NOT one per endpoint — endpoint granularity would create thousands of
tiny near-duplicate rows that FTS-rank poorly. Raw JSON also FTS-indexes badly,
so we extract only the human-readable surface: the module description, the set
of DISTINCT endpoint summaries, and definition property descriptions.

Source trees (gitignored, local raw assets):
  data/F5OS-A API/<Category>/<file>.json
  data/F5OS-C API/<ctx>/<Category>/<file>.json     ctx = cc | partition

GPU-free (FTS5/BM25 lexical ingest). Run from repo root:
  .venv/bin/python scripts/ingest_f5os_api.py --dry-run   # preview, no writes
  .venv/bin/python scripts/ingest_f5os_api.py             # ingest + report
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from utils.db import upsert_document

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "db" / "knowledge.db"

# Build-date tokens baked into the clouddocs JSON URLs (from each README). Used
# only to reconstruct provenance URLs; re-read the README on a future refresh.
PLATFORMS = {
    "F5OS-A API": {
        "platform": "F5OS-A",
        "label": "rSeries",
        "url_base": "https://clouddocs.f5.com/api/rseries-api/f5os-a-apis/2026.04.29",
        "id_prefix": "f5os-a",
        "has_ctx": False,
    },
    "F5OS-C API": {
        "platform": "F5OS-C",
        "label": "VELOS",
        "url_base": "https://clouddocs.f5.com/api/velos-api/f5os-c-apis/2025.04.21",
        "id_prefix": "f5os-c",
        "has_ctx": True,  # extra cc|partition level
    },
}

MAX_CONTENT = 16000  # cap one module doc; BM25 length-normalizes, but bound bloat


def _clean(s: str | None) -> str:
    return " ".join((s or "").split())


# OpenConfig/IETF modules embed RFC 7317 license boilerplate in info.description;
# cut it — it adds FTS noise (copyright/redistribution tokens) and no F5 signal.
_LICENSE_MARKERS = (
    "Portions of this code were derived",
    "Copyright (c) IETF",
    "This version of this YANG module is part of",
    "Redistribution and use in source and binary",
)


def _strip_license(desc: str) -> str:
    cut = len(desc)
    for m in _LICENSE_MARKERS:
        i = desc.find(m)
        if i != -1:
            cut = min(cut, i)
    return desc[:cut].strip()


def _distinct_ordered(items):
    seen, out = set(), []
    for x in items:
        x = _clean(x)
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def extract(spec: dict) -> tuple[str, str]:
    """Return (module_title, content_text) from a Swagger 2.0 spec dict."""
    info = spec.get("info", {}) or {}
    mod_title = _clean(info.get("title")) or "f5os-module"
    mod_desc = _strip_license(_clean(info.get("description")))

    # Distinct endpoint summaries (5 HTTP methods per path reuse one summary).
    summaries = []
    for _path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for _m, op in methods.items():
            if isinstance(op, dict):
                summaries.append(op.get("summary") or op.get("description"))
    summaries = _distinct_ordered(summaries)

    # Definition property descriptions — often the richest semantic text.
    prop_descs = []
    for _dn, dv in (spec.get("definitions") or {}).items():
        if isinstance(dv, dict):
            for _pn, pv in (dv.get("properties") or {}).items():
                if isinstance(pv, dict) and pv.get("description"):
                    prop_descs.append(pv["description"])
    prop_descs = _distinct_ordered(prop_descs)

    parts = [mod_title]
    if mod_desc:
        parts.append(mod_desc)
    if summaries:
        parts.append("Endpoints: " + " | ".join(summaries))
    if prop_descs:
        parts.append("Fields: " + " | ".join(prop_descs))
    content = "\n\n".join(parts)
    return mod_title, content[:MAX_CONTENT]


def iter_specs():
    """Yield (cfg, ctx_or_None, category, json_path) for every spec file."""
    data = REPO / "data"
    for root_name, cfg in PLATFORMS.items():
        root = data / root_name
        if not root.is_dir():
            continue
        for jp in sorted(root.rglob("*.json")):
            rel = jp.relative_to(root).parts  # (ctx?, Category, file) or (Category, file)
            if cfg["has_ctx"]:
                if len(rel) < 3:
                    continue
                ctx, category = rel[0], rel[1]
            else:
                if len(rel) < 2:
                    continue
                ctx, category = None, rel[0]
            yield cfg, ctx, category, jp


def build_record(cfg, ctx, category, jp):
    spec = json.loads(jp.read_text(encoding="utf-8"))
    mod_title, content = extract(spec)
    stem = jp.stem
    if ctx:
        doc_id = f"{cfg['id_prefix']}:{ctx}/{category}/{stem}"
        url = f"{cfg['url_base']}/{ctx}/{category}/{stem}.json"
        ctx_kw = [ctx]
    else:
        doc_id = f"{cfg['id_prefix']}:{category}/{stem}"
        url = f"{cfg['url_base']}/{category}/{stem}.json"
        ctx_kw = []
    # Lead the title with the distinct file stem (the real F5 module name an
    # engineer searches) — NOT info.title, which is a generic, colliding
    # OpenConfig name and would be suppressed by the route's title-dedupe.
    scope = "/".join([*ctx_kw, category])
    title = f"{cfg['platform']} API · {stem} · {scope}"
    keywords = json.dumps(
        [cfg["platform"], cfg["label"], category, *ctx_kw, mod_title, "F5OS API", "swagger"]
    )
    local_path = str(jp.relative_to(REPO))
    return dict(
        source="f5os_api",
        doc_id=doc_id,
        title=title,
        url=url,
        section=category,
        keywords=keywords,
        content=content,
        local_path=local_path,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="preview, no DB writes")
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()

    records = [build_record(*t) for t in iter_specs()]
    print(f"discovered {len(records)} F5OS API module specs")

    if args.dry_run:
        from collections import Counter

        by = Counter(r["source"] for r in records)
        plats = Counter(r["doc_id"].split(":")[0] for r in records)
        lens = [len(r["content"]) for r in records]
        print(f"by platform: {dict(plats)}")
        print(f"content len: min={min(lens)} median={sorted(lens)[len(lens)//2]} max={max(lens)}")
        print(f"\n--- {args.samples} sample docs ---")
        for r in records[: args.samples] + records[-1:]:
            print(f"\n[doc_id] {r['doc_id']}")
            print(f"[title]  {r['title']}")
            print(f"[url]    {r['url']}")
            print(f"[kw]     {r['keywords']}")
            print(f"[content {len(r['content'])} chars]\n{r['content'][:500]}…")
        return

    for r in records:
        upsert_document(DB, **r)
    # Belt-and-suspenders FTS consistency after a batch (upsert maintains it
    # incrementally, but rebuild guarantees no drift — cheap relative to corpus).
    import sqlite3

    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM documents WHERE source='f5os_api'").fetchone()[0]
    print(f"ingested; documents WHERE source='f5os_api' = {n}; FTS rebuilt")


if __name__ == "__main__":
    main()
