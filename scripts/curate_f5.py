#!/usr/bin/env python3
"""
scripts/curate_f5.py — F5 CloudDocs scraper and knowledge curation script.

Crawls clouddocs.f5.com, converts HTML to Markdown, and indexes documents
in the knowledge SQLite database. Respects a 90-day cache TTL per document.

Usage:
    source .venv/bin/activate
    python scripts/curate_f5.py [--config config/scraper_config.yaml] [--force]

Options:
    --config PATH   Path to scraper config YAML (default: config/scraper_config.yaml)
    --force         Ignore cache TTL and re-fetch all documents
    --dry-run       Show what would be fetched without downloading
"""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from loguru import logger
from markdownify import markdownify as md
from tqdm import tqdm

# Add project root to path for utils
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.utils.cache import is_stale
from scripts.utils.db import init_db, upsert_document

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
logger.add("logs/curate_f5.log", rotation="10 MB", retention="30 days", level="DEBUG")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def fetch_page(url: str, session: requests.Session, delay: float = 1.5) -> str | None:
    """Fetch a URL and return HTML content, or None on failure."""
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        time.sleep(delay)
        return resp.text
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None


def extract_links(html: str, base_url: str, allowed_domain: str) -> list[str]:
    """Extract internal links from an HTML page."""
    soup = BeautifulSoup(html, "lxml")
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        # Only follow links within the same domain, strip fragments
        if parsed.netloc == allowed_domain:
            clean = parsed._replace(fragment="").geturl()
            if clean not in links:
                links.append(clean)
    return links


def html_to_markdown(html: str, url: str) -> tuple[str, str]:
    """
    Parse HTML, extract title and convert body to Markdown.
    Returns (title, markdown_content).
    """
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title else url

    # Remove nav, header, footer noise
    for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.find("body")
    body_html = str(main) if main else str(soup)
    markdown = md(body_html, heading_style="ATX", strip=["a"])

    return title, markdown


def save_raw(content: str, url: str, raw_dir: Path) -> Path:
    """Save raw HTML to disk. Returns the file path."""
    filename = urlparse(url).path.strip("/").replace("/", "_") or "index"
    filename = filename[:200] + ".html"
    out_path = raw_dir / filename
    out_path.write_text(content, encoding="utf-8")
    return out_path


def save_processed(content: str, url: str, processed_dir: Path) -> Path:
    """Save Markdown to disk. Returns the file path."""
    filename = urlparse(url).path.strip("/").replace("/", "_") or "index"
    filename = filename[:200] + ".md"
    out_path = processed_dir / filename
    out_path.write_text(content, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Schema fetching (AS3, DO, TS, CFE from F5 Extension snippet URLs)
# ---------------------------------------------------------------------------

def fetch_schemas(schemas: list[dict], session: requests.Session, cfg: dict, force: bool) -> None:
    db_path = cfg["storage"]["db_path"]
    processed_dir = Path(cfg["storage"]["f5_processed_dir"]) / "schemas"
    processed_dir.mkdir(parents=True, exist_ok=True)
    ttl = cfg["cache"]["ttl_days"]

    for schema in schemas:
        url = schema["url"]
        name = schema["name"]
        doc_id = f"schema:{name}"

        if not force and not is_stale(db_path, doc_id, ttl):
            logger.info(f"[SKIP] Schema {name} — cache fresh")
            continue

        logger.info(f"[FETCH] Schema {name}")
        raw = fetch_page(url, session, delay=0.5)
        if not raw:
            continue

        out_path = processed_dir / f"{name}.json"
        out_path.write_text(raw, encoding="utf-8")

        upsert_document(
            db_path,
            source="f5",
            doc_id=doc_id,
            title=name,
            url=url,
            section="schema",
            keywords=json.dumps(["schema", "atc", name]),
            content=raw,
            local_path=str(out_path),
        )
        logger.info(f"[SAVED] Schema {name} → {out_path}")


# ---------------------------------------------------------------------------
# Main crawl
# ---------------------------------------------------------------------------

def crawl_target(
    target: dict,
    session: requests.Session,
    cfg: dict,
    visited: set,
    force: bool,
    dry_run: bool,
) -> None:
    """BFS crawl of a single F5 CloudDocs target section."""
    base_url = target["url"]
    section = target["section"]
    allowed_domain = urlparse(cfg["f5"]["base_url"]).netloc
    delay = cfg["f5"]["crawl_delay"]
    db_path = cfg["storage"]["db_path"]
    ttl = cfg["cache"]["ttl_days"]
    raw_dir = Path(cfg["storage"]["f5_raw_dir"]) / section
    processed_dir = Path(cfg["storage"]["f5_processed_dir"]) / section
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    queue = [base_url]

    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        if not force and not is_stale(db_path, url, ttl):
            logger.info(f"[SKIP] {url}")
            continue

        if dry_run:
            logger.info(f"[DRY-RUN] Would fetch: {url}")
            continue

        logger.info(f"[FETCH] {url}")
        html = fetch_page(url, session, delay=delay)
        if not html:
            continue

        # Discover sub-links within this section
        new_links = extract_links(html, url, allowed_domain)
        for link in new_links:
            if link not in visited and urlparse(link).path.startswith(urlparse(base_url).path):
                queue.append(link)

        # Parse and store
        title, markdown = html_to_markdown(html, url)
        raw_path = save_raw(html, url, raw_dir)
        md_path = save_processed(markdown, url, processed_dir)

        upsert_document(
            db_path,
            source="f5",
            doc_id=url,
            title=title,
            url=url,
            section=section,
            keywords=json.dumps([section, "f5", "bigip"]),
            content=html,
            local_path=str(md_path),
        )
        logger.info(f"[SAVED] {title[:60]} → {md_path.name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="F5 CloudDocs knowledge curation")
    parser.add_argument("--config", default="config/scraper_config.yaml")
    parser.add_argument("--force", action="store_true", help="Ignore cache TTL")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    cfg = load_config(args.config)

    # Initialise DB
    init_db(cfg["storage"]["db_path"])
    Path("logs").mkdir(exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": cfg["f5"]["user_agent"]})

    # Fetch ATC schemas first
    logger.info("=== Fetching ATC Schemas ===")
    fetch_schemas(cfg["f5"]["schemas"], session, cfg, args.force)

    # Crawl each target section
    visited: set[str] = set()
    targets = cfg["f5"]["targets"]
    logger.info(f"=== Crawling {len(targets)} F5 CloudDocs sections ===")

    for target in tqdm(targets, desc="F5 Sections"):
        logger.info(f"--- Section: {target['section']} ({target['url']}) ---")
        crawl_target(target, session, cfg, visited, args.force, args.dry_run)

    total = len(visited)
    logger.info(f"=== F5 Curation Complete — {total} pages processed ===")


if __name__ == "__main__":
    main()
