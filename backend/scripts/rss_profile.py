"""Measure RSS growth across repeated analyze-pipeline runs in one process.

Reproduces the P1 backend memory accumulation reported since session 10
(~2.5 GB RSS / 4.6 GB peak after a handful of analyses in the live
FastAPI worker). Running the pipeline N times in a single python
interpreter mimics the long-lived `uvicorn` process and prints the delta
after each iteration, so fixes can be verified without restarting
systemd.

Usage:
    source .venv/bin/activate
    python backend/scripts/rss_profile.py <archive_path> [iterations]

Defaults to 3 iterations. After each iteration the current RSS, the
delta since iteration 0, and the top tracemalloc allocation buckets are
printed. A final `gc.collect()` + `malloc_trim` (via ctypes) gives us a
best-effort floor so we can separate "live objects" from "arena
fragmentation".
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import resource
import sys
import tracemalloc
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qkview_analyzer.extractor import extract_qkview
from qkview_analyzer.indexer import LogIndexer
from qkview_analyzer.parser import parse_all_logs, parse_f5os_event_log
from qkview_analyzer.reporter import Reporter
from qkview_analyzer.rule_engine import RuleEngine


def _silent(_msg: str) -> None:
    pass


def _rss_mb() -> float:
    # ru_maxrss on Linux is kilobytes.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _rss_current_mb() -> float:
    # Current RSS (not max) from /proc/self/status — max tracks peak, current
    # reveals whether memory actually got released.
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def _malloc_trim() -> bool:
    """Best-effort return of freed glibc arenas to the OS. Returns True on
    non-zero trim (something was released), False if nothing to release or
    the libc hook is unavailable (non-glibc)."""
    libc_path = ctypes.util.find_library("c")
    if not libc_path:
        return False
    libc = ctypes.CDLL(libc_path)
    if not hasattr(libc, "malloc_trim"):
        return False
    libc.malloc_trim.argtypes = [ctypes.c_size_t]
    libc.malloc_trim.restype = ctypes.c_int
    return bool(libc.malloc_trim(0))


def run_pipeline(archive: str) -> None:
    """Mirror main.py's worker() pipeline — no FastAPI, no threads."""
    data = extract_qkview(archive, progress_callback=_silent)
    entries = parse_all_logs(data.log_files, progress_callback=_silent)

    is_f5os = data.meta.product.startswith("F5OS")
    if is_f5os:
        if data.f5os_event_log:
            entries.extend(parse_f5os_event_log(data.f5os_event_log, source_file="event-log.log"))
        if data.f5os_system_events:
            entries.extend(parse_f5os_event_log(data.f5os_system_events, source_file="system-events"))
        entries.sort(key=lambda e: e.timestamp)

    indexer = LogIndexer()
    try:
        indexer.bulk_insert(entries, progress_callback=_silent)
        engine = RuleEngine(platform="f5os" if is_f5os else "tmos")
        findings = engine.scan(indexer, progress_callback=_silent)
        queried = indexer.query(min_severity="warning", limit=5000)
        _ = Reporter.to_json(data.meta, queried, findings, None, qkview_data=data)
    finally:
        indexer.close()


def main(archive: str, iterations: int) -> None:
    tracemalloc.start(25)

    baseline_current = _rss_current_mb()
    baseline_peak = _rss_mb()
    print(f"baseline:  current={baseline_current:8.1f} MB  peak={baseline_peak:8.1f} MB")

    prev_current = baseline_current
    for i in range(1, iterations + 1):
        run_pipeline(archive)
        gc.collect()
        trimmed = _malloc_trim()

        current = _rss_current_mb()
        peak = _rss_mb()
        delta_since_baseline = current - baseline_current
        delta_since_prev = current - prev_current
        print(
            f"iter {i}:     current={current:8.1f} MB  peak={peak:8.1f} MB  "
            f"Δ-baseline={delta_since_baseline:+7.1f}  Δ-prev={delta_since_prev:+7.1f}  "
            f"malloc_trim={'yes' if trimmed else 'no'}"
        )
        prev_current = current

        snapshot = tracemalloc.take_snapshot()
        top = snapshot.statistics("lineno")[:5]
        for stat in top:
            print(f"            {stat.size / 1024 / 1024:6.1f} MB  {stat.traceback[0]}")

    tracemalloc.stop()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: rss_profile.py <archive_path> [iterations]", file=sys.stderr)
        sys.exit(2)
    archive_path = sys.argv[1]
    iters = int(sys.argv[2]) if len(sys.argv) >= 3 else 3
    main(archive_path, iters)
