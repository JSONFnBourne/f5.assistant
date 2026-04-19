"""Golden-file regression tests for the F5OS extractor.

These tests run end-to-end ``extract_qkview`` against the three F5OS qkview
shapes we currently support (rSeries host, VELOS partition, VELOS syscon)
and pin the metadata + structural invariants we care about most:

  * the running F5OS version pulled from PRODUCT (not from `show system
    licensing`, which reports the *licensed* version even when the running
    image has been upgraded),
  * the platform identifier — VELOS partition archives must report the
    local ``controller`` platform, not whichever blade happens to appear
    first in the peer-qkview wrapper,
  * the ``k8s_*`` subpackage skip — VELOS syscon archives ship ~40
    kubernetes / openshift pod wrappers we deliberately exclude from
    discovery and from the log index.

Each archive is extracted exactly once per test session via the
session-scoped fixtures in ``conftest.py``. Extracting all three takes
2–6 minutes wall-clock on this host; if the archives are missing the
tests are skipped automatically.

Baseline values come from session 7 of SESSION_STATE.md (2026-04-18).
Update them when the source archives change.
"""

from __future__ import annotations

import pytest


# ── rSeries host (F5OS-A 1.8.3 / R5R1X) ───────────────────────────────────


class TestRSeriesHost:
    def test_product_is_f5os_a(self, rseries_host_data):
        assert rseries_host_data.meta.product == "F5OS-A"

    def test_running_version_from_product_file(self, rseries_host_data):
        # PRODUCT file reports the running image, which the rSeries was
        # upgraded to before this qkview was collected. `show system
        # licensing` reported a stale 1.6.x — picking it up here would mean
        # we regressed back to the licensing fallback.
        assert rseries_host_data.meta.version == "1.8.3"
        assert rseries_host_data.meta.build == "23453"

    def test_platform_is_appliance_model(self, rseries_host_data):
        # rSeries reports an "RxRyZ" platform code (e.g. R5R1X for an r5000
        # appliance). `controller` would mean we somehow latched onto a
        # VELOS PRODUCT file — a real bug if it ever happens.
        assert rseries_host_data.meta.platform == "R5R1X"

    def test_hostname_populated(self, rseries_host_data):
        assert rseries_host_data.meta.hostname

    def test_quick_link_commands_captured(self, rseries_host_data):
        # `show running-config` is the headline iHealth Quick-Link entry
        # and is present on every F5OS subpackage manifest. If it's missing
        # we've regressed manifest discovery.
        keys = list(rseries_host_data.f5os_commands.keys())
        assert any("running-config" in k for k in keys), keys

    def test_rseries_quick_link_union(self, rseries_host_data):
        # Commands unique-ish to rSeries in the iHealth Quick-Links union:
        # `show lacp` (all three platforms, was missing from the previous
        # allowlist) and `show system health` (rSeries + syscon).
        keys = set(rseries_host_data.f5os_commands.keys())
        assert any("show lacp" in k for k in keys), keys
        assert any("show system health" in k for k in keys), keys
        assert any("show cluster" in k for k in keys), keys
        assert any("show tenants" in k for k in keys), keys
        assert any("show interfaces" in k for k in keys), keys


# ── VELOS partition (F5OS-C 1.8.2 / controller, double-nested) ────────────


class TestVelosPartition:
    def test_product_is_f5os_c(self, velos_partition_data):
        assert velos_partition_data.meta.product == "F5OS-C"

    def test_running_version_from_product_file(self, velos_partition_data):
        # Same upgrade story: PRODUCT says 1.8.2/28311; licensing said 1.6.2.
        assert velos_partition_data.meta.version == "1.8.2"
        assert velos_partition_data.meta.build == "28311"

    def test_local_platform_wins_over_peer(self, velos_partition_data):
        # VELOS partition archives are nested under a peer-qkview wrapper.
        # The local platform here is the controller; the peer subpackages
        # belong to blades. If we ever start reporting a blade ID we've
        # broken the local-vs-peer priority in `_extract_f5os`.
        assert velos_partition_data.meta.platform == "controller"

    def test_hostname_populated(self, velos_partition_data):
        assert velos_partition_data.meta.hostname

    def test_no_k8s_labels_in_logs(self, velos_partition_data):
        # The k8s_* skip should keep kubernetes pod logs out of our index.
        # VELOS partition itself ships no k8s_*, but a regression that
        # admits them in `_f5os_should_extract` would break this on syscon
        # and then leak into partition extracts via shared discovery code.
        offenders = [k for k in velos_partition_data.log_files if "k8s_" in k]
        assert offenders == []

    def test_partition_quick_link_union(self, velos_partition_data):
        # VELOS partition's iHealth Quick-Links panel centres on redundancy
        # (HA blade pairing) and the L2 plumbing — lacp, interfaces, vlans,
        # service-pods. None of these were in the pre-allowlist widening
        # set, so an empty capture here is a silent regression.
        keys = set(velos_partition_data.f5os_commands.keys())
        assert any("show system redundancy" in k for k in keys), keys
        assert any("show lacp" in k for k in keys), keys
        assert any("show interfaces" in k for k in keys), keys
        assert any("show vlans" in k for k in keys), keys
        # `show service-pods` matches the broader `show service` entry.
        assert any("show service" in k for k in keys), keys


# ── VELOS syscon (F5OS-C 1.8.2 / controller, k8s_* present) ───────────────


class TestVelosSyscon:
    def test_product_is_f5os_c(self, velos_syscon_data):
        assert velos_syscon_data.meta.product == "F5OS-C"

    def test_running_version_from_product_file(self, velos_syscon_data):
        assert velos_syscon_data.meta.version == "1.8.2"
        assert velos_syscon_data.meta.build == "28311"

    def test_platform_is_controller(self, velos_syscon_data):
        # VELOS syscon is the chassis controller view — platform should
        # always read "controller" regardless of how many blade subpackages
        # the archive carries.
        assert velos_syscon_data.meta.platform == "controller"

    def test_hostname_populated(self, velos_syscon_data):
        assert velos_syscon_data.meta.hostname

    def test_k8s_subpackages_excluded_from_logs(self, velos_syscon_data):
        # Syscon archives ship ~40 kubernetes/openshift pod wrappers under
        # `subpackages/k8s_*`. They carry zero F5 signal and pollute the
        # log index with kubernetes / openshift chatter — the skip in
        # `_f5os_should_extract` and the matching guard in
        # `_discover_f5os_subpackage_prefixes._walk` prevent that. If
        # either regresses we'll see "k8s_" appear in the log labels.
        offenders = [k for k in velos_syscon_data.log_files if "k8s_" in k]
        assert offenders == [], offenders

    def test_quick_link_commands_captured(self, velos_syscon_data):
        keys = list(velos_syscon_data.f5os_commands.keys())
        assert any("running-config" in k for k in keys), keys

    def test_syscon_chassis_commands_captured(self, velos_syscon_data):
        # Chassis-specific commands that only exist on VELOS syscon's
        # `vcc-confd` subpackage. These are the gaps called out in
        # QKVIEW_FORMATS.md §F5OS gaps — missing them means the allowlist
        # widening regressed, or peer-qkview priority is picking a blade
        # partition manifest over the local `vcc-confd` one.
        keys = set(velos_syscon_data.f5os_commands.keys())
        assert any("show slots" in k for k in keys), keys
        assert any("show ctrlr_status" in k for k in keys), keys
        assert any("show system chassis-macs" in k for k in keys), keys
        assert any("show system blade-power" in k for k in keys), keys
        assert any("show system redundancy" in k for k in keys), keys
        # `show partitions` is the Quick-Links summary for VELOS syscon and
        # also catches `show partitions volumes` / `show partitions install`
        # via substring match.
        assert any("show partitions" in k for k in keys), keys


# ── Cross-archive sanity ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fixture_name",
    ["rseries_host_data", "velos_partition_data", "velos_syscon_data"],
)
def test_meta_product_recognises_known_f5os_family(request, fixture_name):
    """Every F5OS archive should land in F5OS-A or F5OS-C — never the
    bare ``"F5OS"`` placeholder. A bare placeholder means the PRODUCT
    file lookup found nothing, which silently demotes us to the
    licensing fallback."""
    data = request.getfixturevalue(fixture_name)
    assert data.meta.product in {"F5OS-A", "F5OS-C"}, (
        f"{fixture_name} reported product={data.meta.product!r} — "
        "PRODUCT file lookup probably failed"
    )
