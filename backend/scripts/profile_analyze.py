"""Time each stage of the analyze pipeline on a given archive.

Promoted from `/var/tmp/profile_analyze.py` (session-10) so the tool
survives reboots. Usage:

    source .venv/bin/activate
    python backend/scripts/profile_analyze.py <archive_path>

Prints one line per stage with wall-clock seconds and the cardinality the
stage produced. Used to spot quadratic / per-entry regressions before
they ship — pairs with tests/test_timing_guards.py which pins the two
dominant stages against budgets.
"""

import sys
import time
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


def main(archive: str) -> None:
    t0 = time.time()
    print(f"== {archive}")

    tA = time.time()
    data = extract_qkview(archive, progress_callback=_silent)
    print(
        f"[1] extract_qkview        {time.time() - tA:6.2f}s  "
        f"log_files={len(data.log_files)} commands={len(data.f5os_commands)}"
    )

    tA = time.time()
    entries = parse_all_logs(data.log_files, progress_callback=_silent)
    print(f"[2] parse_all_logs        {time.time() - tA:6.2f}s  entries={len(entries)}")

    is_f5os = data.meta.product.startswith("F5OS")
    if is_f5os:
        tA = time.time()
        if data.f5os_event_log:
            entries.extend(parse_f5os_event_log(data.f5os_event_log, source_file="event-log.log"))
        if data.f5os_system_events:
            entries.extend(parse_f5os_event_log(data.f5os_system_events, source_file="system-events"))
        entries.sort(key=lambda e: e.timestamp)
        print(f"[3] f5os_event + sort     {time.time() - tA:6.2f}s  total_entries={len(entries)}")

    tA = time.time()
    indexer = LogIndexer()
    indexer.bulk_insert(entries, progress_callback=_silent)
    print(f"[4] indexer.bulk_insert   {time.time() - tA:6.2f}s")

    tA = time.time()
    platform = "f5os" if is_f5os else "tmos"
    engine = RuleEngine(platform=platform)
    findings = engine.scan(indexer, progress_callback=_silent)
    print(
        f"[5] rule_engine.scan      {time.time() - tA:6.2f}s  "
        f"findings={len(findings)}  platform={platform}  rules_loaded={len(engine.rules)}"
    )

    tA = time.time()
    queried = indexer.query(min_severity="warning", limit=5000)
    print(f"[6] indexer.query(5000)   {time.time() - tA:6.2f}s  rows={len(queried)}")

    tA = time.time()
    json_str = Reporter.to_json(data.meta, queried, findings, None, qkview_data=data)
    print(f"[7] Reporter.to_json      {time.time() - tA:6.2f}s  json_kb={len(json_str) / 1024:.1f}")

    indexer.close()
    print(f"TOTAL                     {time.time() - t0:6.2f}s")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: profile_analyze.py <archive_path>", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])
