#!/usr/bin/env python3
"""
scripts/ingest_techdocs.py — Crawl F5 techdocs.f5.com manual pages into knowledge.db.

TWO MODES:

1. TOC mode (recommended for bulk ingestion from K000130285):
   Pass a file of TOC page URLs (one per line).  The script fetches each TOC
   page, discovers all chapter links within that manual's URL namespace, then
   ingests every chapter.  No need to list chapters manually.

   Usage:
       python scripts/ingest_techdocs.py --toc-urls scripts/techdocs_toc_urls.txt

   To build techdocs_toc_urls.txt:
     - Open https://my.f5.com/manage/s/article/K000130285 in your browser
     - View page source (Ctrl+U)
     - Copy all hrefs pointing to techdocs.f5.com into the file, one per line

2. Manifest mode (for explicit chapter lists, e.g. old kb/en-us/products/ format):
   Uses the MANIFEST list embedded in this script.

   Usage:
       python scripts/ingest_techdocs.py [--manifest PATH]

Common options:
    --dry-run     Fetch and parse without writing to the database.
    --force       Re-index URLs already present in the database.
    --delay SECS  Seconds between requests (default 0.5).
    --concurrency N  Parallel fetches per batch (default 1, increase with care).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

# ── Config ─────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent.parent / "db" / "knowledge.db"
SOURCE = "techdocs"
DELAY_S = 0.5

HEADERS = {"User-Agent": "F5-KB-Ingestor/1.0 (+internal-knowledge-base)"}

# Embedded manifest for the old kb/en-us/products/ URL format (APM config guide).
# These were already ingested.  Add new old-format pages here if found.
MANIFEST: list[tuple[str, str]] = [
    # APM Configuration Guide 11.4.0 — already ingested
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_intro.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_webappaccmgt.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_resources.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_understanding.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_creatingpolicies.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_general_actions.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_client_checks.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_server_checks.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_clientcert_auth.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_virtualserver.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_advanced_policies.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_loggingandreporting.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_snmp.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_sessionvars.html",
    ),
    (
        "apm-config-guide",
        "https://techdocs.f5.com/kb/en-us/products/big-ip_apm/manuals/product/apm-config-11-4-0/apm_config_irules.html",
    ),
]

# ── TOC discovery ──────────────────────────────────────────────────────────────


def _toc_url_to_section(toc_url: str) -> str:
    """Derive a human-readable section tag from the TOC URL path."""
    path = urlparse(toc_url).path.rstrip("/")
    # e.g. /en-us/bigip-17-1-0/big-ip-system-dos-protection-... → dos-protection
    stem = Path(
        path
    ).stem  # e.g. big-ip-system-dos-protection-and-protocol-firewall-implementations
    # Trim common prefixes for a shorter tag
    stem = re.sub(r"^big-ip-system-", "", stem)
    stem = re.sub(r"^big-ip-", "", stem)
    return stem[:60]


def _chapter_prefix(toc_url: str) -> str:
    """
    Return the URL path prefix that all chapter pages within this manual share.

    For /en-us/bigip-17-1-0/big-ip-system-dos.../  → /en-us/bigip-17-1-0/big-ip-system-dos.../
    For /kb/en-us/products/big-ip_apm/.../apm-config-11-4-0/ → that directory
    """
    parsed = urlparse(toc_url)
    path = parsed.path.rstrip("/")
    # TOC page is the .html file; chapters live one level deeper (same stem as dir name).
    # Prefix = path without the .html extension + "/"
    if path.endswith(".html"):
        return path[: -len(".html")] + "/"
    return path + "/"


def discover_chapters(toc_url: str, session: requests.Session) -> list[str]:
    """
    Fetch a TOC page and return all chapter URLs found within the same manual namespace.
    Strips fragment anchors and deduplicates.
    """
    try:
        resp = session.get(toc_url, timeout=20, headers=HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR fetching TOC {toc_url}: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    prefix = _chapter_prefix(toc_url)
    base = f"{urlparse(toc_url).scheme}://{urlparse(toc_url).netloc}"

    found: list[str] = []
    seen_paths: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Resolve relative links
        full = urljoin(toc_url, href)
        # Strip fragment
        full, _ = urldefrag(full)
        parsed = urlparse(full)

        # Must be same host (techdocs.f5.com) and under the manual prefix
        if parsed.netloc not in ("techdocs.f5.com", ""):
            continue
        if not parsed.path.startswith(prefix):
            continue
        if not parsed.path.endswith(".html"):
            continue
        # Exclude the TOC page itself
        toc_path = urlparse(toc_url).path
        if parsed.path == toc_path:
            continue
        # Deduplicate by path
        if parsed.path in seen_paths:
            continue
        seen_paths.add(parsed.path)
        found.append(f"{base}{parsed.path}")

    return found


# ── HTML parsing ───────────────────────────────────────────────────────────────

CONTENT_SELECTORS = [
    "div.body",
    "div#content",
    "div.content",
    "article",
    "main",
    "div.topic",
]

STRIP_CLASSES = [
    "navfooter",
    "navheader",
    "breadcrumb",
    "toc",
    "related-links",
    "shortdesc",
    "navigation",
]


def _url_to_doc_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    stem = Path(path).stem
    parts = path.split("/")
    manual_seg = parts[-2] if len(parts) >= 2 else "techdocs"
    return f"techdocs:{manual_seg}/{stem}"


def _extract_keywords(soup: BeautifulSoup) -> str:
    kws: list[str] = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        kws.append(tag.get_text(" ", strip=True).lower())
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw and meta_kw.get("content"):
        kws.append(str(meta_kw["content"]))
    return " ".join(kws)[:2000]


def fetch_and_parse(url: str, session: requests.Session) -> dict | None:
    try:
        resp = session.get(url, timeout=20, headers=HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  SKIP (fetch error): {e}", file=sys.stderr)
        return None

    # Quick 404 detection for F5's soft-404 pages
    if "page you are looking for does not exist" in resp.text:
        print("  SKIP (soft 404)", file=sys.stderr)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Title
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else urlparse(url).path

    # Main content
    body: Tag | None = None
    for sel in CONTENT_SELECTORS:
        body = soup.select_one(sel)
        if body:
            break
    if body is None:
        body = soup.find("body") or soup

    # Strip chrome
    for tag in body.find_all(["nav", "script", "style", "header", "footer", "aside"]):
        tag.decompose()
    for cls in STRIP_CLASSES:
        for tag in body.find_all(attrs={"class": cls}):
            tag.decompose()

    content = body.get_text("\n", strip=True)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()

    if len(content) < 150:
        print(f"  SKIP (content too short: {len(content)} chars)", file=sys.stderr)
        return None

    keywords = _extract_keywords(soup)
    content_hash = hashlib.md5(content.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    return {
        "doc_id": _url_to_doc_id(url),
        "title": title,
        "url": url,
        "source": SOURCE,
        "section": "",
        "keywords": keywords,
        "content": content,
        "content_hash": content_hash,
        "last_fetched": now,
        "created_at": now,
    }


# ── DB helpers ─────────────────────────────────────────────────────────────────

INSERT_SQL = """
INSERT INTO documents
    (source, doc_id, title, url, section, keywords, content_hash, local_path,
     last_fetched, created_at, content)
VALUES
    (:source, :doc_id, :title, :url, :section, :keywords, :content_hash, '',
     :last_fetched, :created_at, :content)
"""

UPDATE_SQL = """
UPDATE documents
SET title=:title, keywords=:keywords, content=:content,
    content_hash=:content_hash, last_fetched=:last_fetched
WHERE url=:url
"""


def existing_urls(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT url FROM documents WHERE source='techdocs'").fetchall()
    return {r[0] for r in rows}


def upsert(conn: sqlite3.Connection, doc: dict, seen: set[str], dry_run: bool) -> str:
    """Insert or update; returns 'inserted', 'updated', or 'error'."""
    if dry_run:
        return "inserted"
    try:
        if doc["url"] in seen:
            conn.execute(UPDATE_SQL, doc)
            return "updated"
        else:
            conn.execute(INSERT_SQL, doc)
            return "inserted"
    except sqlite3.Error as e:
        print(f"  DB error: {e}", file=sys.stderr)
        return "error"


def rebuild_fts(conn: sqlite3.Connection) -> None:
    print("Rebuilding FTS index...", end=" ", flush=True)
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
    conn.commit()
    print("done.")


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--toc-urls",
        type=Path,
        default=None,
        help="File of TOC page URLs (one per line). Chapters auto-discovered.",
    )
    parser.add_argument(
        "--manifest", type=Path, default=None, help="File of explicit chapter URLs (one per line)."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force", action="store_true", help="Re-index URLs already in the database."
    )
    parser.add_argument(
        "--delay", type=float, default=DELAY_S, help=f"Seconds between requests (default {DELAY_S})"
    )
    parser.add_argument("--no-reembed", action="store_true", help="skip the dense-index refresh")
    args = parser.parse_args()

    session = requests.Session()
    conn = sqlite3.connect(DB_PATH) if not args.dry_run else None
    seen: set[str] = existing_urls(conn) if conn else set()

    work: list[tuple[str, str]] = []  # (section, chapter_url)

    # ── Mode: TOC URL list ─────────────────────────────────────────────────────
    if args.toc_urls:
        toc_lines = [
            l.strip()
            for l in args.toc_urls.read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        print(f"TOC pages to crawl: {len(toc_lines)}")
        for toc_url in toc_lines:
            section = _toc_url_to_section(toc_url)
            print(f"\n→ TOC: {toc_url.split('/')[-1]}  [section={section}]")
            chapters = discover_chapters(toc_url, session)
            if not chapters:
                print("  WARNING: no chapters discovered — check URL or manual slug")
                continue
            print(f"  Found {len(chapters)} chapters")
            for ch in chapters:
                work.append((section, ch))
            time.sleep(args.delay)

    # ── Mode: explicit manifest file ───────────────────────────────────────────
    elif args.manifest:
        for line in args.manifest.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                work.append(("manual", line))

    # ── Mode: embedded MANIFEST ────────────────────────────────────────────────
    else:
        work = list(MANIFEST)

    print(f"\nTotal chapter pages to ingest: {len(work)}")
    if not work:
        print("Nothing to do.")
        return

    inserted = updated = skipped = errors = 0

    for section, url in work:
        label = "/".join(url.split("/")[-2:])
        print(f"\n  → {label}")

        if not args.force and url in seen:
            print("    already indexed, skipping")
            skipped += 1
            continue

        doc = fetch_and_parse(url, session)
        if doc is None:
            errors += 1
            time.sleep(args.delay)
            continue

        doc["section"] = section
        print(f"    title: {doc['title'][:75]}")
        print(f"    chars: {len(doc['content'])}")

        result = upsert(conn, doc, seen, args.dry_run)
        if result == "inserted":
            inserted += 1
            seen.add(url)
        elif result == "updated":
            updated += 1
        else:
            errors += 1

        if not args.dry_run:
            conn.commit()
        time.sleep(args.delay)

    print(f"\n{'─'*55}")
    print(f"Inserted: {inserted}  Updated: {updated}  " f"Skipped: {skipped}  Errors: {errors}")

    if conn and (inserted + updated) > 0:
        rebuild_fts(conn)

    if conn:
        conn.close()

    if conn and (inserted + updated) > 0 and not args.no_reembed:
        from build_embeddings import refresh_index_quiet

        refresh_index_quiet()

    if args.dry_run:
        print("(dry-run — no changes written)")


if __name__ == "__main__":
    main()
