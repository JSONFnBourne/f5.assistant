"""Minimal tmstat snapshot parser for top resource consumers."""

from dataclasses import dataclass


@dataclass
class TmstatSummary:
    """Summary of tmstat performance data."""
    snapshot_count: int = 0
    time_ranges: list[str] = None
    categories: list[str] = None

    def __post_init__(self):
        if self.time_ranges is None:
            self.time_ranges = []
        if self.categories is None:
            self.categories = []


def parse_tmstat_files(tmstat_files: dict[str, bytes]) -> TmstatSummary:
    """Parse tmstat snapshot files for summary info.

    tmstat files are binary F5-proprietary format. For now, we extract
    high-level metadata (count, time ranges, categories) from the file paths.
    Full binary parsing would require F5 SDK tools.

    Args:
        tmstat_files: dict of {path: binary_content}

    Returns:
        TmstatSummary with available metadata
    """
    summary = TmstatSummary(snapshot_count=len(tmstat_files))

    categories = set()
    time_ranges = set()

    for path in tmstat_files.keys():
        # Path format: shared/tmstat/snapshots/blade0/TYPE/INTERVAL/filename
        parts = path.split("/")
        if len(parts) >= 5:
            # Category is the type (public, performance)
            category = parts[3] if len(parts) > 3 else ""
            if category:
                categories.add(category)

            # Time range from the interval dir
            interval = parts[4] if len(parts) > 4 else ""
            if interval:
                time_ranges.add(interval)

    summary.categories = sorted(categories)
    summary.time_ranges = sorted(time_ranges)

    return summary
