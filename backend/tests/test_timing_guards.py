"""Timing guardrails for the two pipeline stages that dominate analyze runtime.

Session 9 introduced a quadratic regression in `rule_engine.scan` that slid
past review and was only caught by standalone profiling in session 10.
Session 11's parser rework shifted `parse_all_logs` hot-path cost too. These
two tests pin both stages against generous budgets on `velos_partition_data`
(the heaviest fixture archive) so a similar silent regression trips pytest
instead of reaching users.

Budgets are intentionally ~2× the observed post-session-11 baselines to
tolerate cold caches and slower CI boxes without hiding real regressions.
Bump the threshold here only after a real perf improvement; don't loosen
it to paper over a slowdown.
"""

from __future__ import annotations

import time

import pytest

from qkview_analyzer.indexer import LogIndexer
from qkview_analyzer.parser import parse_all_logs
from qkview_analyzer.rule_engine import RuleEngine


PARSE_BUDGET_SECONDS = 30.0
SCAN_BUDGET_SECONDS = 20.0


@pytest.fixture(scope="module")
def indexed_velos_partition(velos_partition_data):
    """Parse + index the VELOS partition fixture once for timing the scan
    phase in isolation. Building the indexer is itself expensive — time it
    here (outside the scan test body) so the scan budget measures only the
    rule loop, not the setup cost."""
    entries = parse_all_logs(velos_partition_data.log_files)
    indexer = LogIndexer()
    indexer.bulk_insert(entries)
    yield indexer
    indexer.close()


class TestPipelineTimingGuards:
    def test_parse_all_logs_under_budget(self, velos_partition_data):
        start = time.perf_counter()
        entries = parse_all_logs(velos_partition_data.log_files)
        elapsed = time.perf_counter() - start

        assert entries, "no entries parsed from velos_partition fixture archive"
        assert elapsed < PARSE_BUDGET_SECONDS, (
            f"parse_all_logs took {elapsed:.2f}s on velos_partition "
            f"(budget {PARSE_BUDGET_SECONDS}s; post-session-11 baseline ~10-15s). "
            f"Parsed {len(entries)} entries from {len(velos_partition_data.log_files)} files."
        )

    def test_rule_engine_scan_under_budget(self, indexed_velos_partition):
        engine = RuleEngine(platform="f5os")
        start = time.perf_counter()
        findings = engine.scan(indexed_velos_partition)
        elapsed = time.perf_counter() - start

        # Finding count is not the subject of this test — the scan budget is.
        # Session-9's quadratic bug showed up as O(rules × entries²) growth,
        # so failures here most likely mean a regression in _evaluate_rule
        # or indexer.query fan-out.
        assert elapsed < SCAN_BUDGET_SECONDS, (
            f"rule_engine.scan took {elapsed:.2f}s on velos_partition "
            f"(budget {SCAN_BUDGET_SECONDS}s; post-session-10 baseline ~5-10s). "
            f"Scanned {len(engine.rules)} rules, produced {len(findings)} findings."
        )
