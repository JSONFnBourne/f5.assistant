#!/usr/bin/env python3
"""
scripts/curate_rfc.py — RFC Editor bulk downloader and knowledge curation script.

Downloads the complete RFC index XML, then fetches ALL RFC text documents,
storing them locally and indexing metadata in the knowledge SQLite database.
Respects a 90-day cache TTL per RFC — only re-fetches stale or missing docs.

Usage:
    source .venv/bin/activate
    python scripts/curate_rfc.py [--config config/scraper_config.yaml] [--force]

Options:
    --config PATH   Path to scraper config YAML (default: config/scraper_config.yaml)
    --force         Ignore cache TTL and re-fetch all RFCs
    --dry-run       Parse index and show what would be downloaded (no downloads)
    --limit N       Only process first N RFCs (useful for testing)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import aiofiles
import aiohttp
import requests
import yaml
from loguru import logger
from tqdm import tqdm

# Add project root to path for utils
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.utils.cache import is_stale
from scripts.utils.db import init_db, upsert_document

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
)
logger.add("logs/curate_rfc.log", rotation="10 MB", retention="30 days", level="DEBUG")

# RFC XML namespace
RFC_NS = {"rfc": "https://www.rfc-editor.org/rfc-index"}


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------


def fetch_rfc_index(index_url: str, index_dir: Path) -> Path:
    """Download the RFC index XML and cache it locally."""
    index_dir.mkdir(parents=True, exist_ok=True)
    out_path = index_dir / "rfc-index.xml"
    logger.info(f"Downloading RFC index from {index_url}")
    resp = requests.get(index_url, timeout=60, stream=True)
    resp.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    logger.info(f"RFC index saved → {out_path} ({out_path.stat().st_size:,} bytes)")
    return out_path


def parse_rfc_index(index_path: Path) -> list[dict]:
    """
    Parse rfc-index.xml and return a list of RFC metadata dicts.
    Handles both namespaced and non-namespaced XML.
    """
    tree = ET.parse(index_path)
    root = tree.getroot()

    # Detect namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    rfcs = []
    for entry in root.findall(f"{ns}rfc-entry"):

        def txt(tag: str) -> str | None:
            el = entry.find(f"{ns}{tag}")
            return el.text.strip() if el is not None and el.text else None

        def txt_list(tag: str, inner: str) -> list[str]:
            return [c.text.strip() for c in entry.findall(f"{ns}{tag}/{ns}{inner}") if c.text]

        doc_id_raw = txt("doc-id")
        if not doc_id_raw:
            continue

        number = doc_id_raw.replace("RFC", "").lstrip("0") or "0"

        rfcs.append(
            {
                "doc_id": doc_id_raw.lower(),  # e.g. 'rfc793'
                "number": number,  # e.g. '793'
                "title": txt("title"),
                "status": txt("current-status"),
                "date_year": txt("date/year"),
                "date_month": txt("date/month"),
                "keywords": txt_list("keywords", "kw"),
                "authors": txt_list("author", "name"),
                "obsoletes": txt_list("obsoletes", "doc-id"),
                "obsoleted_by": txt_list("obsoleted-by", "doc-id"),
                "updates": txt_list("updates", "doc-id"),
                "updated_by": txt_list("updated-by", "doc-id"),
            }
        )

    logger.info(f"Parsed {len(rfcs):,} RFC entries from index")
    return rfcs


# ---------------------------------------------------------------------------
# Async downloading
# ---------------------------------------------------------------------------


async def download_rfc(
    session: aiohttp.ClientSession,
    rfc: dict,
    doc_url_template: str,
    docs_dir: Path,
    db_path: str,
    ttl: int,
    force: bool,
    semaphore: asyncio.Semaphore,
    delay: float,
    results: dict,
) -> None:
    """Download a single RFC text file asynchronously."""
    doc_id = rfc["doc_id"]
    number = rfc["number"]
    out_path = docs_dir / f"{doc_id}.txt"

    # Check cache
    if not force and not is_stale(db_path, doc_id, ttl):
        results["skipped"] += 1
        return

    url = doc_url_template.format(number=number)

    async with semaphore:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 404:
                    logger.debug(f"[404] {doc_id} — not available as text")
                    results["not_found"] += 1
                    return
                resp.raise_for_status()
                content = await resp.text(encoding="utf-8", errors="replace")
                await asyncio.sleep(delay)

            # Write to disk
            async with aiofiles.open(out_path, "w", encoding="utf-8") as f:
                await f.write(content)

            # Index in DB
            keywords_json = json.dumps(rfc.get("keywords", []) + [rfc.get("status", ""), "rfc"])
            upsert_document(
                db_path,
                source="rfc",
                doc_id=doc_id,
                title=rfc.get("title"),
                url=url,
                section=rfc.get("status"),
                keywords=keywords_json,
                content=content,
                local_path=str(out_path),
            )
            results["downloaded"] += 1
            logger.debug(f"[SAVED] {doc_id} — {rfc.get('title', '')[:50]}")

        except Exception as e:
            logger.warning(f"[ERROR] {doc_id}: {e}")
            results["errors"] += 1


async def download_all_rfcs(
    rfcs: list[dict],
    cfg: dict,
    db_path: str,
    force: bool,
    dry_run: bool,
    limit: int | None,
) -> None:
    """Async batch download all RFCs with concurrency control."""
    docs_dir = Path(cfg["storage"]["rfc_docs_dir"])
    docs_dir.mkdir(parents=True, exist_ok=True)

    doc_url_template = cfg["rfc"]["doc_url_template"]
    ttl = cfg["cache"]["ttl_days"]
    concurrency = cfg["rfc"]["concurrent_downloads"]
    delay = cfg["rfc"]["download_delay"]

    if limit:
        rfcs = rfcs[:limit]
        logger.info(f"Limiting to first {limit} RFCs")

    if dry_run:
        stale_count = sum(1 for r in rfcs if is_stale(db_path, r["doc_id"], ttl))
        logger.info(f"[DRY-RUN] {stale_count:,} of {len(rfcs):,} RFCs would be downloaded")
        return

    results = {"downloaded": 0, "skipped": 0, "not_found": 0, "errors": 0}
    semaphore = asyncio.Semaphore(concurrency)

    connector = aiohttp.TCPConnector(limit=concurrency, ssl=True)
    headers = {"User-Agent": "KnowledgeBaseBot/1.0 (internal; research)"}

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [
            download_rfc(
                session,
                rfc,
                doc_url_template,
                docs_dir,
                db_path,
                ttl,
                force,
                semaphore,
                delay,
                results,
            )
            for rfc in rfcs
        ]
        for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="RFCs"):
            await coro

    logger.info(
        f"=== RFC Download Complete ===\n"
        f"  Downloaded : {results['downloaded']:,}\n"
        f"  Skipped    : {results['skipped']:,} (cache fresh)\n"
        f"  Not found  : {results['not_found']:,}\n"
        f"  Errors     : {results['errors']:,}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="RFC knowledge curation")
    parser.add_argument("--config", default="config/scraper_config.yaml")
    parser.add_argument("--force", action="store_true", help="Ignore cache TTL")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of RFCs")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db_path = cfg["storage"]["db_path"]
    index_dir = Path(cfg["storage"]["rfc_index_dir"])

    # Initialise
    init_db(db_path)
    Path("logs").mkdir(exist_ok=True)

    # Step 1: Download RFC index XML
    index_path = fetch_rfc_index(cfg["rfc"]["index_url"], index_dir)

    # Step 2: Parse index
    rfcs = parse_rfc_index(index_path)

    # Step 3: Save parsed index as JSON for quick reference
    index_json = index_dir / "rfc-index.json"
    index_json.write_text(json.dumps(rfcs, indent=2), encoding="utf-8")
    logger.info(f"RFC JSON index → {index_json}")

    # Step 4: Download all RFC documents (async, with cache)
    logger.info(f"=== Starting RFC document download ({len(rfcs):,} total) ===")
    asyncio.run(download_all_rfcs(rfcs, cfg, db_path, args.force, args.dry_run, args.limit))


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    main()
