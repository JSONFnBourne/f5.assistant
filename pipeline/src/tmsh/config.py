"""Central configuration objects for the tmsh project scaffold."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List, Optional


PROJECT_NAME: str = "tmsh"


@dataclass(frozen=True)
class DirectoryLayout:
    """Resolved project directories.

    The scaffold keeps all mutable artefacts outside of version control unless
    explicitly required.  Paths are resolved relative to the project root by
    passing :class:`pathlib.Path` instances to :meth:`from_project_root`.
    """

    root: Path
    data_raw: Path
    data_processed: Path
    data_chunks: Path
    data_training: Path
    logs: Path
    scripts: Path

    @classmethod
    def from_project_root(cls, root: Path) -> "DirectoryLayout":
        return cls(
            root=root,
            data_raw=root / "data" / "raw",
            data_processed=root / "data" / "processed",
            data_chunks=root / "data" / "chunks",
            data_training=root / "data" / "training",
            logs=root / "log",
            scripts=root / "scripts",
        )


@dataclass(frozen=True)
class ScrapeTarget:
    """Description of a documentation subtree to collect."""

    name: str
    url: str
    depth: int


@dataclass(frozen=True)
class ScraperConfig:
    """Configuration for scraping and caching behaviour."""

    targets: Iterable[ScrapeTarget]
    user_agent: str = (
        "tmsh-scaffold/0.1 (+https://github.com/owner/tmsh; contact=maintainers@tmsh.local)"
    )
    cache_expiry: timedelta = field(default=timedelta(days=120))
    metadata_path: Optional[Path] = None

    def with_metadata_path(self, directory_layout: DirectoryLayout) -> "ScraperConfig":
        if self.metadata_path is not None:
            return self
        return ScraperConfig(
            targets=self.targets,
            user_agent=self.user_agent,
            cache_expiry=self.cache_expiry,
            metadata_path=directory_layout.logs / "scrape_history.json",
        )


SCRAPE_TARGETS: List[ScrapeTarget] = [
    ScrapeTarget(
        name="general",
        url="https://clouddocs.f5.com/cli/tmsh-reference/latest/general/",
        depth=1,
    ),
    ScrapeTarget(
        name="commands",
        url="https://clouddocs.f5.com/cli/tmsh-reference/latest/commands/",
        depth=1,
    ),
    ScrapeTarget(
        name="modules",
        url="https://clouddocs.f5.com/cli/tmsh-reference/latest/modules/",
        depth=2,
    ),
]
