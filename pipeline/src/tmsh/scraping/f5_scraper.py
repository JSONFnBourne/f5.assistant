"""Scraper for the F5 tmsh documentation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from ..config import DirectoryLayout, ScrapeTarget, ScraperConfig

IGNORED_BOILERPLATE: Tuple[str, ...] = (
    "COPYRIGHT\n       No part of this program may be reproduced or transmitted in any form or by any means, electronic or mechanical, including\n       photocopying, recording, or information storage and retrieval systems, for any purpose other than the purchaser's personal\n       use, without the express written permission of F5 Networks, Inc.\n\n       F5 Networks and BIG-IP (c) Copyright 2018. All rights reserved.\n\nBIG-IP\t\t\t\t    2018-07-10\t\t\t\tapi-protection response(1)",
    "If you are looking to move beyond–or simply bypass–the theory and would like to find complex examples to reference, be sure to check out the CodeShare to find a plethora of ways to put iRules to work. \nThe BIG-IP API Reference documentation contains community-contributed content. F5 does not monitor or control community code contributions. We make no guarantees or warranties regarding the available code, and it may contain errors, defects, bugs, inaccuracies, or security vulnerabilities. Your access to and use of any code available in the BIG-IP API reference guides is solely at your own risk.",
)


@dataclass
class ScrapeResult:
    """Structured representation of a fetched document."""

    url: str
    path: Path
    collected_at: datetime


class F5Scraper:
    """High level interface for collecting tmsh documentation pages."""

    def __init__(self, layout: DirectoryLayout, config: ScraperConfig) -> None:
        self.layout = layout
        self.config = config.with_metadata_path(layout)
        assert self.config.metadata_path is not None
        self._metadata_path = self.config.metadata_path
        self._metadata: Dict[str, str] = self._load_metadata()

    # ------------------------------------------------------------------
    # public API
    def run(self) -> List[ScrapeResult]:
        results: List[ScrapeResult] = []
        for target in self.config.targets:
            results.extend(self._scrape_target(target))
        self._save_metadata()
        return results

    def clear_cache(self) -> None:
        """Forget previously scraped timestamps to force a full refresh."""

        self._metadata.clear()

    # ------------------------------------------------------------------
    def _scrape_target(self, target: ScrapeTarget) -> List[ScrapeResult]:
        pending: List[Tuple[str, int]] = [(target.url, 0)]
        visited: Set[str] = set()
        results: List[ScrapeResult] = []

        while pending:
            url, depth = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)
            if not self._should_fetch(url):
                continue

            html = self._fetch(url)
            if html is None:
                continue

            cleaned_html, links = self._extract_and_filter(url, html)
            output_path = self._write_raw_html(target.name, url, cleaned_html)
            timestamp = datetime.now(timezone.utc)
            self._metadata[url] = timestamp.isoformat()
            results.append(ScrapeResult(url=url, path=output_path, collected_at=timestamp))

            if depth < target.depth:
                for link in links:
                    if self._is_same_domain(target.url, link):
                        pending.append((link, depth + 1))

        return results

    # ------------------------------------------------------------------
    def _should_fetch(self, url: str) -> bool:
        if url not in self._metadata:
            return True
        last_collected = datetime.fromisoformat(self._metadata[url])
        age = datetime.now(timezone.utc) - last_collected
        return age >= self.config.cache_expiry

    def _fetch(self, url: str) -> Optional[str]:
        headers = {"User-Agent": self.config.user_agent}
        try:
            with httpx.Client(follow_redirects=True, headers=headers, timeout=30.0) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.text
        except httpx.HTTPError:
            return None

    def _extract_and_filter(self, url: str, html: str) -> Tuple[str, List[str]]:
        soup = BeautifulSoup(html, "html.parser")
        main = soup.select_one("div.wy-nav-content div[role='main'] div.section")
        if main is None:
            main = soup

        green_area = self._select_green_area(main)
        if green_area is None:
            green_area = main

        self._remove_boilerplate(green_area)
        links = [urljoin(url, a["href"]) for a in green_area.find_all("a", href=True)]
        return green_area.prettify(), links

    def _select_green_area(self, container: Tag) -> Optional[Tag]:
        """Return the element that matches the visual "green box" instructions."""

        # Preference order: unordered lists, tables, and definition lists inside the section.
        for selector in ("ul", "ol", "table", "div.toctree-wrapper", "section"):
            element = container.select_one(selector)
            if element is not None:
                return element
        return None

    def _remove_boilerplate(self, element: Tag) -> None:
        text_content = element.get_text(separator="\n")
        for snippet in IGNORED_BOILERPLATE:
            if snippet in text_content:
                for text_node in element.find_all(string=True):
                    if snippet in text_node:
                        text_node.extract()

    def _write_raw_html(self, target_name: str, url: str, html: str) -> Path:
        parsed = urlparse(url)
        filename = Path(parsed.path.strip("/"))
        if not filename.name:
            filename /= "index"
        filename = filename.with_suffix(".html")
        output_dir = self.layout.data_raw / target_name / filename.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename.name
        output_path.write_text(html, encoding="utf-8")
        return output_path

    def _is_same_domain(self, base_url: str, candidate: str) -> bool:
        return urlparse(base_url).netloc == urlparse(candidate).netloc

    def _load_metadata(self) -> Dict[str, str]:
        if not self._metadata_path.exists():
            return {}
        try:
            return json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_metadata(self) -> None:
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self._metadata_path.write_text(json.dumps(self._metadata, indent=2, sort_keys=True), encoding="utf-8")
