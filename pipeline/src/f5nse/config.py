from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_SOURCE_CONFIG = Path("configs/crawl_sources.yaml")


@dataclass(slots=True)
class SourceSpec:
    """Configuration for a crawl seed."""

    url: str
    max_depth: int
    ttl_days: int = 90
    allowed_domains: Sequence[str] | None = None

    def domains(self) -> Sequence[str]:
        if self.allowed_domains:
            return self.allowed_domains
        return ()


def load_sources(path: Path | None = None) -> list[SourceSpec]:
    """Load crawl seed configuration from YAML."""
    config_path = path or DEFAULT_SOURCE_CONFIG
    data = yaml.safe_load(config_path.read_text())
    specs: list[SourceSpec] = []
    if not data:
        return specs
    for entry in data:
        specs.append(
            SourceSpec(
                url=entry["url"],
                max_depth=int(entry.get("max_depth", 1)),
                ttl_days=int(entry.get("ttl_days", 90)),
                allowed_domains=tuple(entry.get("allowed_domains", []) or []),
            )
        )
    return specs


def dump_sources(path: Path, specs: Iterable[SourceSpec]) -> None:
    """Serialize crawl seeds to YAML."""
    serializable = []
    for spec in specs:
        serializable.append(
            {
                "url": spec.url,
                "max_depth": spec.max_depth,
                "ttl_days": spec.ttl_days,
                "allowed_domains": list(spec.domains()),
            }
        )
    path.write_text(yaml.safe_dump(serializable, sort_keys=False))
