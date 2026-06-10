from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import aiohttp
import tyro
from bs4 import BeautifulSoup

from .config import DEFAULT_SOURCE_CONFIG, SourceSpec, load_sources

LOGGER = logging.getLogger("irule.scrape")


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class CrawlState:
    """Tracks crawl metadata and enforces TTL windows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS fetch_log(
                url TEXT PRIMARY KEY,
                fetched_ts REAL NOT NULL
            )
            """)
        self.conn.commit()

    def should_fetch(self, url: str, ttl_days: int, force_refresh: bool) -> bool:
        if force_refresh:
            return True
        cur = self.conn.execute("SELECT fetched_ts FROM fetch_log WHERE url = ?", (url,))
        row = cur.fetchone()
        if not row:
            return True
        last_ts = row[0]
        age_days = (time.time() - last_ts) / 86400.0
        return age_days >= ttl_days

    def mark_fetched(self, url: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO fetch_log(url, fetched_ts) VALUES (?, ?)",
            (url, time.time()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> CrawlState:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class RobotsCache:
    """Caches and enforces robots.txt directives per host."""

    def __init__(self, session: aiohttp.ClientSession, user_agent: str) -> None:
        self.session = session
        self.user_agent = user_agent
        self.cache: dict[str, RobotFileParser] = {}
        self.crawl_delay: dict[str, float] = {}
        self.lock = asyncio.Lock()

    async def allow(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc
        async with self.lock:
            parser = self.cache.get(host)
            if not parser:
                parser = await self._fetch_robots(parsed)
                self.cache[host] = parser
        return parser.can_fetch(self.user_agent, url)

    def delay_for(self, url: str) -> float:
        host = urlparse(url).netloc
        return self.crawl_delay.get(host, 0.0)

    async def _fetch_robots(self, parsed) -> RobotFileParser:
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            async with self.session.get(robots_url, timeout=20) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    parser.parse(text.splitlines())
                    delay = parser.crawl_delay(self.user_agent)
                    if delay:
                        self.crawl_delay[parsed.netloc] = delay
                else:
                    parser.parse([])
        except aiohttp.ClientError:
            parser.parse([])
        return parser


def normalize_link(link: str, base_url: str) -> str | None:
    if not link:
        return None
    joined = urljoin(base_url, link)
    joined, _ = urldefrag(joined)
    parsed = urlparse(joined)
    if parsed.scheme not in {"http", "https"}:
        return None
    return joined


def extract_links(html: str, base_url: str, allowed_domains: Sequence[str]) -> Iterator[str]:
    soup = BeautifulSoup(html, "html.parser")
    domains = set(allowed_domains)
    base_host = urlparse(base_url).netloc
    if not domains:
        domains.add(base_host)
    for anchor in soup.find_all("a", href=True):
        href = normalize_link(anchor.get("href"), base_url)
        if not href:
            continue
        host = urlparse(href).netloc
        if host in domains:
            yield href


def should_skip_content_type(content_type: str | None, allowed_types: Sequence[str]) -> bool:
    if not content_type:
        return False
    return not any(ct in content_type for ct in allowed_types)


def write_payload(output_dir: Path, url: str, depth: int, payload: dict) -> Path:
    sha = hashlib.sha1(url.encode("utf-8")).hexdigest()
    out_path = output_dir / f"{sha}.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return out_path


def path_allows_extension(url: str, allowed_extensions: Sequence[str]) -> bool:
    path = urlparse(url).path.lower()
    if not path or path.endswith("/"):
        return True
    return any(path.endswith(ext) for ext in allowed_extensions)


@dataclass
class ScrapeArgs:
    source_config: Path = DEFAULT_SOURCE_CONFIG
    output_dir: Path = Path("data/raw")
    state_path: Path = Path("state/crawl_state.sqlite3")
    concurrency: int = 2
    request_timeout: float = 20.0
    user_agent: str = "irule-crawler/0.1 (+https://github.com/JSONFnBourne/irule)"
    max_pages: int | None = None
    throttle_seconds: float = 1.0
    respect_robots: bool = True
    force_refresh: bool = False
    skip_tls_verify: bool = False
    allowed_content_types: Sequence[str] = ("text/html", "text/plain", "application/json")
    allowed_extensions: Sequence[str] = (".html", ".htm", ".shtml", ".xhtml")
    accept_header: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
    log_level: str = "INFO"


async def fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    request_timeout: float,
) -> tuple[str | None, dict[str, str]]:
    try:
        async with session.get(url, timeout=request_timeout) as resp:
            if resp.status != 200:
                LOGGER.warning("Skipping %s (status %s)", url, resp.status)
                return None, {}
            content_type = resp.headers.get("Content-Type", "")
            try:
                text = await resp.text(errors="ignore")
            except UnicodeDecodeError:
                raw = await resp.read()
                text = raw.decode(resp.charset or "utf-8", errors="ignore")
            return text, {"content_type": content_type, "status": resp.status}
    except asyncio.TimeoutError:
        LOGGER.warning("Timeout fetching %s", url)
    except aiohttp.ClientError as exc:
        LOGGER.warning("Client error fetching %s: %s", url, exc)
    return None, {}


async def crawl_source(
    session: aiohttp.ClientSession,
    state: CrawlState,
    robots: RobotsCache,
    spec: SourceSpec,
    args: ScrapeArgs,
) -> dict[str, int]:
    queue: deque[tuple[str, int]] = deque()
    queue.append((spec.url, 0))
    visited: set[str] = set()
    stats = {"fetched": 0, "skipped": 0}
    host_last_fetch: dict[str, float] = {}
    while queue:
        if args.max_pages and stats["fetched"] >= args.max_pages:
            break
        url, depth = queue.popleft()
        if depth > spec.max_depth:
            continue
        if url in visited:
            continue
        visited.add(url)

        parsed = urlparse(url)
        if args.respect_robots and not await robots.allow(url):
            LOGGER.info("Robots disallow %s", url)
            stats["skipped"] += 1
            continue

        if not state.should_fetch(url, spec.ttl_days, args.force_refresh):
            LOGGER.debug("TTL skip %s", url)
            stats["skipped"] += 1
            continue

        host = parsed.netloc
        delay = max(args.throttle_seconds, robots.delay_for(url))
        elapsed = time.time() - host_last_fetch.get(host, 0.0)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)

        html, meta = await fetch_page(session, url, args.request_timeout)
        host_last_fetch[host] = time.time()
        if not html:
            stats["skipped"] += 1
            continue

        if should_skip_content_type(meta.get("content_type"), args.allowed_content_types):
            LOGGER.info("Skipping %s due to content-type %s", url, meta.get("content_type"))
            stats["skipped"] += 1
            continue

        payload = {
            "url": url,
            "source_root": spec.url,
            "retrieved_at": utc_now().isoformat(),
            "depth": depth,
            "html": html,
            "metadata": meta,
        }
        write_payload(args.output_dir, url, depth, payload)
        state.mark_fetched(url)
        stats["fetched"] += 1

        if depth < spec.max_depth:
            allowed_domains = list(spec.domains()) or [urlparse(spec.url).netloc]
            for link in extract_links(html, url, allowed_domains):
                if not path_allows_extension(link, args.allowed_extensions):
                    LOGGER.debug("Skipping %s due to extension filter", link)
                    continue
                if link not in visited:
                    queue.append((link, depth + 1))
    return stats


async def run_scraper(args: ScrapeArgs) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    specs = load_sources(args.source_config)
    if not specs:
        LOGGER.warning("No sources configured in %s", args.source_config)
        return
    connector = aiohttp.TCPConnector(ssl=not args.skip_tls_verify, limit_per_host=args.concurrency)
    timeout = aiohttp.ClientTimeout(total=None)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers={"User-Agent": args.user_agent, "Accept": args.accept_header},
    ) as session:
        with CrawlState(args.state_path) as state:
            robots_cache = RobotsCache(session, args.user_agent)
            total_stats = {"fetched": 0, "skipped": 0}
            for spec in specs:
                stats = await crawl_source(session, state, robots_cache, spec, args)
                LOGGER.info("Crawled %s -> %s", spec.url, stats)
                total_stats["fetched"] += stats["fetched"]
                total_stats["skipped"] += stats["skipped"]
            LOGGER.info("Scrape finished: %s", total_stats)


def main() -> None:
    args = tyro.cli(ScrapeArgs)
    asyncio.run(run_scraper(args))


if __name__ == "__main__":
    main()
