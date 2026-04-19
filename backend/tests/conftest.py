"""Shared fixtures for the qkview-analyzer test suite.

These tests run against real F5OS qkview archives shipped in
``F5/data/qkview/``. Extracting and analyzing one archive takes 10s–2m, so
the fixtures are session-scoped: each archive is loaded exactly once per
test session and reused across every assertion.

If the archives are missing (CI / fresh checkout), the tests that depend on
them are skipped — there's no way to fabricate a representative F5OS
qkview, and we don't want to commit 1.5 GB of binary fixtures to git.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Allow `from qkview_analyzer import …` from the test files without needing
# an editable install in CI.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qkview_analyzer.extractor import QKViewData, extract_qkview  # noqa: E402

_QKVIEW_DIR = Path(__file__).resolve().parents[2] / "data" / "qkview"

# Map a short fixture name → archive filename. The names are stable; the
# files change as the user collects fresh qkviews.
_ARCHIVES = {
    "rseries_host":      "rSeries.tar",
    "velos_partition":   "partition.tar",
    "velos_syscon":      "syscon.tar",
    "tmos_post_upgrade": "tmos_ve.qkview",
}


def _archive_path(short_name: str) -> Path:
    return _QKVIEW_DIR / _ARCHIVES[short_name]


@pytest.fixture(scope="session", autouse=True)
def _f5os_tempdir(tmp_path_factory) -> Path:
    """Pin the F5OS extraction tempdir under a disk-backed location.

    `tmp_path_factory` defaults to /tmp, which on this host is a 16 GB
    tmpfs with ~150 MB free — far too small for a fully-extracted F5OS
    qkview (2–3 GB after the allowlist filter). We deliberately bypass
    the pytest temp factory and stage under /var/tmp on the root disk.
    The directory is cleaned up at session end via
    ``shutil.rmtree(..., ignore_errors=True)``.
    """
    import shutil
    import tempfile
    base = Path(tempfile.mkdtemp(prefix="pytest_f5os_", dir="/var/tmp"))
    os.environ["F5_QKVIEW_TMPDIR"] = str(base)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _extract_or_skip(short_name: str) -> QKViewData:
    path = _archive_path(short_name)
    if not path.exists():
        pytest.skip(f"qkview archive not present: {path.name}")
    return extract_qkview(path)


@pytest.fixture(scope="session")
def rseries_host_data() -> QKViewData:
    return _extract_or_skip("rseries_host")


@pytest.fixture(scope="session")
def velos_partition_data() -> QKViewData:
    return _extract_or_skip("velos_partition")


@pytest.fixture(scope="session")
def velos_syscon_data() -> QKViewData:
    return _extract_or_skip("velos_syscon")


@pytest.fixture(scope="session")
def tmos_post_upgrade_data() -> QKViewData:
    return _extract_or_skip("tmos_post_upgrade")
