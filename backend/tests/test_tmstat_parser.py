"""Unit tests for tmstat path-metadata parsing (no binary parsing).

Covers both real qkview layouts confirmed across the data/qkview/ fixtures:
  - snapshot layout (i-Series / appliance): shared/tmstat/snapshots/<blade>/<type>/<interval>/<file>
  - live-segment layout (TMOS VE + F5OS subpackages): .../var/tmstat/<scope>/<segment>
"""

from qkview_analyzer.tmstat_parser import parse_tmstat_files, parse_tmstat_path


def test_snapshot_iseries_86400():
    # Real path from data/qkview/iseries.qkview
    p = "shared/tmstat/snapshots/blade0/public/86400/blade0-public-86400-2026-04-15T00:00:00"
    info = parse_tmstat_path(p)
    assert info is not None
    assert info["family"] == "snapshot"
    assert info["blade"] == "blade0"
    assert info["type"] == "public"  # category
    assert info["interval"] == "86400"


def test_snapshot_performance_short_interval():
    # Real path from data/qkview/iseries.qkview — guards against off-by-one on
    # short numeric intervals and a different type.
    p = "shared/tmstat/snapshots/blade0/performance/10/blade0-performance-10-2026-04-15T00:00:00"
    info = parse_tmstat_path(p)
    assert info["family"] == "snapshot"
    assert info["blade"] == "blade0"
    assert info["type"] == "performance"
    assert info["interval"] == "10"


def test_segment_tmos_ve():
    # Real path from data/qkview/tmos_ve.qkview — live segment, no type/interval.
    p = "var/tmstat/blade/bigd_stat_segment.0"
    info = parse_tmstat_path(p)
    assert info is not None
    assert info["family"] == "segment"
    assert info["scope"] == "blade"
    assert info["type"] == ""
    assert info["interval"] == ""
    assert info["segment"] == "bigd_stat_segment.0"


def test_segment_f5os_cluster_encodes_blade_and_type():
    # Real path from data/qkview/rSeries.tar — F5OS subpackage prefix + cluster
    # filename "<blade>-<type>". The anchor logic must skip the subpackage path.
    p = (
        "qkview/subpackages/system_tmstat_merged/qkview/filesystem/"
        "var/tmstat/cluster/blade1-public"
    )
    info = parse_tmstat_path(p)
    assert info["family"] == "segment"
    assert info["scope"] == "cluster"
    assert info["blade"] == "blade1"
    assert info["type"] == "public"
    assert info["interval"] == ""


def test_non_tmstat_path_returns_none():
    assert parse_tmstat_path("config/bigip.conf") is None
    assert parse_tmstat_path("var/log/ltm") is None


def test_aggregate_across_layouts():
    files = {
        "shared/tmstat/snapshots/blade0/public/86400/f1": b"",
        "shared/tmstat/snapshots/blade0/performance/10/f2": b"",
        "var/tmstat/blade/bigd_stat_segment.0": b"",
        "qkview/subpackages/system_tmstat_merged/qkview/filesystem/"
        "var/tmstat/cluster/blade1-public": b"",
    }
    summary = parse_tmstat_files(files)
    assert summary.snapshot_count == 4
    assert summary.categories == ["performance", "public"]
    # Intervals sorted numerically, not lexically ("10" before "86400").
    assert summary.time_ranges == ["10", "86400"]
    assert summary.blades == ["blade0", "blade1"]
