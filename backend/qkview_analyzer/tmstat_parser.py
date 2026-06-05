"""Minimal tmstat path-metadata parser for resource coverage.

Binary TMSS parsing is intentionally out of scope here — qkviews ship tmstat
data in two distinct path layouts and this module derives coverage metadata
(blade / type / interval) from the *paths* only:

  1. Snapshot layout (TMOS appliance, e.g. i-Series):
       shared/tmstat/snapshots/<blade>/<type>/<interval>/<file>
       e.g. shared/tmstat/snapshots/blade0/public/86400/blade0-public-86400-<date>
       - <type>     in {public, performance}
       - <interval> a numeric retention window in seconds {10, 600, 3600, 10800, 86400, ...}

  2. Live-segment layout (TMOS VE + F5OS/VELOS subpackages):
       .../var/tmstat/<scope>/<segment>
       e.g. var/tmstat/blade/bigd_stat_segment.0
            var/tmstat/cluster/blade1-public           (F5OS cluster: <blade>-<type>)
       - <scope>   in {blade, cluster}
       - no interval; cluster files encode <blade>-<type> in the filename.

The old implementation used fixed positional indices (parts[3]/parts[4]) that
were both wrong for the real snapshot layout and silently skipped the
var/tmstat layout entirely. This version locates each field by *role* so it is
robust to the F5OS subpackage prefix (qkview/subpackages/.../filesystem/...)
and to either family.
"""

import re
from dataclasses import dataclass

# A blade directory in the snapshot layout is "blade<N>" (blade0, blade1, ...).
_BLADE_RE = re.compile(r"^blade\d+$")
# F5OS cluster snapshot files are named "<blade>-<type>", e.g. "blade1-public".
_CLUSTER_FILE_RE = re.compile(r"^(blade\d+)-([A-Za-z]\w*)$")


@dataclass
class TmstatSummary:
    """Path-derived coverage summary of tmstat data."""
    snapshot_count: int = 0
    time_ranges: list[str] = None   # distinct numeric intervals (snapshot layout)
    categories: list[str] = None    # distinct types (public / performance)
    blades: list[str] = None        # distinct blades seen across both layouts

    def __post_init__(self):
        if self.time_ranges is None:
            self.time_ranges = []
        if self.categories is None:
            self.categories = []
        if self.blades is None:
            self.blades = []


def parse_tmstat_path(path: str) -> dict | None:
    r"""Derive {family, blade, type, interval, scope, segment} from a tmstat path.

    Field roles, not fixed positions:
      - ``interval`` is the segment that is purely numeric (snapshot layout only).
      - ``type`` is the non-numeric, non-blade segment (public/performance), or
        the suffix of an F5OS ``<blade>-<type>`` cluster filename.
      - ``blade`` matches ``blade\d+`` wherever it appears (a path segment in
        the snapshot layout, or the prefix of a cluster filename).

    Returns None if the path is not a recognisable tmstat member.
    """
    parts = [p for p in path.strip("/").split("/") if p]
    if "tmstat" not in parts:
        return None
    anchor = parts.index("tmstat")
    root = parts[anchor - 1] if anchor > 0 else ""   # "shared" or "var"
    tail = parts[anchor + 1:]
    if not tail:
        return None

    info = {
        "family": "",
        "root": root,
        "blade": "",
        "type": "",
        "interval": "",
        "scope": "",
        "segment": tail[-1],
    }

    if tail[0] == "snapshots":
        # snapshots/<blade>/<type>/<interval>/<file>
        info["family"] = "snapshot"
        middle = tail[1:-1]  # drop "snapshots" and the trailing filename
        for seg in middle:
            if seg.isdigit():
                info["interval"] = seg
            elif _BLADE_RE.match(seg):
                info["blade"] = seg
            else:
                info["type"] = seg
        return info

    # Live-segment layout: <scope>/<segment...>
    info["family"] = "segment"
    info["scope"] = tail[0]            # "blade" or "cluster"
    m = _CLUSTER_FILE_RE.match(info["segment"])
    if m:
        info["blade"] = m.group(1)     # e.g. blade1
        info["type"] = m.group(2)      # e.g. public / performance
    return info


def parse_tmstat_files(tmstat_files: dict[str, bytes]) -> TmstatSummary:
    """Summarise tmstat coverage from the captured member paths.

    Counts files and collects the distinct intervals / types / blades. Binary
    contents are not parsed here.
    """
    summary = TmstatSummary(snapshot_count=len(tmstat_files))

    categories: set[str] = set()
    intervals: set[str] = set()
    blades: set[str] = set()

    for path in tmstat_files.keys():
        info = parse_tmstat_path(path)
        if not info:
            continue
        if info["type"]:
            categories.add(info["type"])
        if info["interval"]:
            intervals.add(info["interval"])
        if info["blade"]:
            blades.add(info["blade"])

    summary.categories = sorted(categories)
    # Sort intervals numerically (they are seconds) for stable, readable output.
    summary.time_ranges = sorted(intervals, key=lambda s: int(s))
    summary.blades = sorted(blades)

    return summary
