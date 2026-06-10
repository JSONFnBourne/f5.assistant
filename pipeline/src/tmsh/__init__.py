"""tmsh project package."""

from .config import (
    PROJECT_NAME,
    SCRAPE_TARGETS,
    DirectoryLayout,
    ScraperConfig,
    ScrapeTarget,
)

__all__ = [
    "PROJECT_NAME",
    "SCRAPE_TARGETS",
    "DirectoryLayout",
    "ScrapeTarget",
    "ScraperConfig",
]
