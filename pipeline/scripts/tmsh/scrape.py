#!/usr/bin/env python
"""Scrape tmsh documentation into ``data/raw``."""
from __future__ import annotations

import argparse
import logging

from tmsh.config import SCRAPE_TARGETS, ScraperConfig
from tmsh.scraping.f5_scraper import F5Scraper
from tmsh.utils import get_directory_layout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Ignore cache and force re-scrape")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    layout = get_directory_layout()
    layout.data_raw.mkdir(parents=True, exist_ok=True)
    config = ScraperConfig(targets=SCRAPE_TARGETS)
    scraper = F5Scraper(layout, config)
    if args.force:
        scraper.clear_cache()
    results = scraper.run()
    logging.basicConfig(level=logging.INFO)
    for result in results:
        logging.info("Saved %s to %s", result.url, result.path)


if __name__ == "__main__":
    main()
