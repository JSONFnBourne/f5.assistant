#!/usr/bin/env python3
"""
scripts/ingest_support_kb.py — Bulk ingest F5 Support KB markdown articles.

Reads markdown files from the Support data directories and loads them into
knowledge.db using efficient batch inserts + a single FTS rebuild at the end.

Sources indexed:
  f5_kb          — Knowledge_Base/ (K-articles, general troubleshooting)
  f5_security    — Security_Advisories/ (CVE / security K-articles)
  xc_techdocs    — XC_TechDocs/ (F5 Distributed Cloud documentation)

Usage:
    cd /home/jsonbourne/projects/Claude/F5
    source .venv/bin/activate
    python scripts/ingest_support_kb.py [--dry-run] [--force]

Options:
    --dry-run    Parse and report counts without writing to the database.
    --force      Re-index articles that are already present in the database.
"""

import argparse
import hashlib
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent.parent / "db" / "knowledge.db"
DATA_ROOT = Path("/home/jsonbourne/projects/Support/data")

SOURCES = [
    {
        "source": "f5_kb",
        "dir": DATA_ROOT / "Knowledge_Base",
        "section": "knowledge-base",
        "base_kw": ["f5", "bigip", "knowledge-base", "troubleshooting"],
    },
    {
        "source": "f5_security",
        "dir": DATA_ROOT / "Security_Advisories",
        "section": "security-advisory",
        "base_kw": ["f5", "bigip", "security", "cve", "vulnerability"],
    },
    {
        "source": "xc_techdocs",
        "dir": DATA_ROOT / "XC_TechDocs",
        "section": "xc-techdocs",
        "base_kw": ["f5", "xc", "distributed-cloud", "f5xc"],
    },
]

# Articles shorter than this are still indexed but may be low-value stubs.
MIN_CONTENT_CHARS = 50

# Batch size for executemany inserts.
BATCH_SIZE = 500

# ── Parsers ───────────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://(?:my\.f5\.com|docs\.f5\.com|clouddocs\.f5\.com)\S+")
_TITLE_RE = re.compile(r"^(K\w+):\s+(.+)$", re.MULTILINE)
_HEADER_RE = re.compile(r"^# Article\s+(K\w+)", re.MULTILINE)
_CVE_RE = re.compile(r"CVE-\d{4}-\d+")


def parse_article(path: Path, base_kw: list[str]) -> dict | None:
    """
    Parse a K-article markdown file.

    Returns a dict ready for DB insertion, or None if the file is too short
    or cannot be parsed.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if len(content) < MIN_CONTENT_CHARS:
        return None

    # doc_id: K-number from filename, e.g. "K14190"
    k_num = path.stem  # e.g. "K14190" or "K000133373"
    doc_id = k_num

    # Title: prefer "KXXXXX: <description>" line, fall back to filename
    title_match = _TITLE_RE.search(content)
    if title_match:
        title = f"{title_match.group(1)}: {title_match.group(2).strip()}"
    else:
        title = k_num

    # URL: first F5 URL found in the document
    url_match = _URL_RE.search(content)
    url = url_match.group(0) if url_match else f"https://my.f5.com/manage/s/article/{k_num}"

    # Keywords: base tags + any CVEs mentioned
    kw = list(base_kw)
    cves = _CVE_RE.findall(content)
    kw.extend(cves)
    # Add K-number itself as a keyword for direct lookup
    kw.append(k_num.lower())
    keywords_json = "[" + ", ".join(f'"{k}"' for k in kw) + "]"

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    return {
        "doc_id": doc_id,
        "title": title[:500],  # cap title length
        "url": url,
        "keywords": keywords_json,
        "content": content,
        "content_hash": content_hash,
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

INSERT_DOC_SQL = """
    INSERT INTO documents
        (source, doc_id, title, url, section, keywords, content_hash,
         content, local_path, last_fetched, created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(doc_id) DO UPDATE SET
        source=excluded.source,
        title=excluded.title,
        url=excluded.url,
        section=excluded.section,
        keywords=excluded.keywords,
        content_hash=excluded.content_hash,
        content=excluded.content,
        local_path=excluded.local_path,
        last_fetched=excluded.last_fetched
"""


def bulk_insert(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(INSERT_DOC_SQL, rows)


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS index from the documents table content."""
    print("  Rebuilding FTS index…", end=" ", flush=True)
    # docs_fts is an external-content FTS5 table: plain DELETE derives the
    # delete-tokens from the *current* content and cannot clean up drift.
    # The supported full-reindex idiom is the special 'rebuild' command.
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
    print("done.")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk ingest F5 Support KB into knowledge.db")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--force", action="store_true", help="Re-index already-present articles")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64 MB page cache

    # Pre-load existing doc_id → content_hash to support incremental ingest.
    # Skipping is hash-based, not presence-based, so edited KB articles re-ingest.
    existing: dict[str, str | None] = {}
    if not args.force:
        rows = conn.execute("SELECT doc_id, content_hash FROM documents").fetchall()
        existing = {r[0]: r[1] for r in rows}
        print(f"Existing documents in DB: {len(existing):,}")

    total_inserted = 0
    total_skipped = 0
    total_errors = 0

    try:
        for src in SOURCES:
            src_dir = src["dir"]
            source = src["source"]
            section = src["section"]
            base_kw = src["base_kw"]

            md_files = sorted(src_dir.glob("*.md"))
            if not md_files:
                print(f"[{source}] No .md files found in {src_dir}")
                continue

            print(f"\n[{source}] {len(md_files):,} files in {src_dir.name}/")

            batch: list[tuple] = []
            inserted = skipped = errors = 0

            for i, path in enumerate(md_files):
                if i % 2000 == 0 and i > 0:
                    print(
                        f"  … {i:,}/{len(md_files):,} processed "
                        f"(+{inserted} new, {skipped} skipped, {errors} errors)"
                    )

                doc_id = path.stem

                article = parse_article(path, base_kw)
                if article is None:
                    errors += 1
                    continue

                # Skip only if present AND unchanged (content_hash match).
                if not args.force and existing.get(doc_id) == article["content_hash"]:
                    skipped += 1
                    continue

                batch.append(
                    (
                        source,
                        article["doc_id"],
                        article["title"],
                        article["url"],
                        section,
                        article["keywords"],
                        article["content_hash"],
                        article["content"],
                        str(path),  # local_path
                        now,  # last_fetched
                        now,  # created_at
                    )
                )

                if len(batch) >= BATCH_SIZE and not args.dry_run:
                    bulk_insert(conn, batch)
                    conn.commit()
                    batch = []

                inserted += 1

            # Flush remaining batch
            if batch and not args.dry_run:
                bulk_insert(conn, batch)
                conn.commit()

            print(
                f"  [{source}] done — {inserted:,} inserted, {skipped:,} skipped, {errors:,} errors"
            )
            total_inserted += inserted
            total_skipped += skipped
            total_errors += errors

        # Rebuild FTS once for all sources
        if not args.dry_run and total_inserted > 0:
            rebuild_fts(conn)
            conn.commit()

        # Final summary
        final_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        print(f"\n{'DRY RUN — ' if args.dry_run else ''}Ingestion complete.")
        print(f"  New documents inserted : {total_inserted:,}")
        print(f"  Already indexed (skip) : {total_skipped:,}")
        print(f"  Parse errors           : {total_errors:,}")
        print(f"  Total documents in DB  : {final_count:,}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
