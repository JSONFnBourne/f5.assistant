"""Pure-function unit tests for extractor helpers.

These run in milliseconds and don't need any qkview archives — they cover
the two pieces most likely to silently regress:

  * ``_f5os_should_extract`` — the allowlist that decides which tar
    members survive the streaming pre-extract pass. Getting this wrong
    silently drops PRODUCT files / manifests / logs and breaks every
    F5OS analysis downstream.
  * ``_parse_f5os_product_file`` — the parser whose output drives the
    "running F5OS version" we report to the user. Format drift or a typo
    in the field-merge logic produces confidently-wrong version strings.
"""

from __future__ import annotations

from qkview_analyzer.extractor import (
    _f5os_prefix_priority,
    _f5os_should_extract,
    _is_quick_link_command,
    _normalize_f5os_command_name,
    _parse_f5os_product_file,
)


class TestShouldExtract:
    def test_root_manifest_kept(self):
        assert _f5os_should_extract("qkview/manifest.json")

    def test_subpackage_manifest_kept(self):
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/manifest.json"
        )

    def test_product_file_kept(self):
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/etc/PRODUCT"
        )
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/etc/PRODUCT.LTS"
        )

    def test_command_output_kept(self):
        assert _f5os_should_extract(
            "qkview/subpackages/system_manager/qkview/commands/abc123/0/out"
        )

    def test_log_files_kept(self):
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/var/log/lopd/lopd.log"
        )
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/var/F5/partition/log/velos.log"
        )
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/var/log_controller/velos.log"
        )

    def test_meminfo_and_version_kept(self):
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/proc/meminfo"
        )
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/version"
        )

    def test_qkview_collect_log_kept(self):
        assert _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/qkview-collect.log"
        )

    def test_k8s_subpackages_skipped(self):
        # VELOS syscon ships ~40 of these; each adds tens of MB of
        # container artefacts and zero F5 signal.
        assert not _f5os_should_extract(
            "qkview/subpackages/k8s_kube-apiserver/qkview/manifest.json"
        )
        assert not _f5os_should_extract(
            "qkview/subpackages/k8s_etcd/qkview/filesystem/etc/PRODUCT"
        )
        assert not _f5os_should_extract(
            "qkview/subpackages/k8s_openshift-apiserver/qkview/commands/h/0/out"
        )

    def test_journal_skipped(self):
        # Binary systemd journals — already excluded for TMOS, same here.
        assert not _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/var/log/journal/abc/system.journal"
        )

    def test_irrelevant_filesystem_paths_skipped(self):
        # Random filesystem snapshots that don't match our allowlist.
        assert not _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/usr/lib/libfoo.so"
        )
        assert not _f5os_should_extract(
            "qkview/subpackages/host-qkview/qkview/filesystem/bin/bash"
        )

    def test_non_subpackage_paths_skipped(self):
        # Anything outside subpackages/ is noise for F5OS.
        assert not _f5os_should_extract("qkview/something_else/foo.txt")
        assert not _f5os_should_extract("README.md")


class TestParseProductFile:
    def test_velos_controller(self):
        content = (
            "Product: F5OS-C\n"
            "Version: 1\n"
            "Release: 8\n"
            "Patch: 2\n"
            "Build: 28311\n"
            "Platform: controller\n"
            "Tag: LTS\n"
        )
        product, version, build, platform = _parse_f5os_product_file(content)
        assert product == "F5OS-C"
        assert version == "1.8.2"
        assert build == "28311"
        assert platform == "controller"

    def test_rseries_appliance(self):
        content = (
            "Product: F5OS-A\n"
            "Version: 1\n"
            "Release: 8\n"
            "Patch: 3\n"
            "Build: 23453\n"
            "Platform: R5R1X\n"
        )
        product, version, build, platform = _parse_f5os_product_file(content)
        assert product == "F5OS-A"
        assert version == "1.8.3"
        assert build == "23453"
        assert platform == "R5R1X"

    def test_missing_patch_field_still_parses(self):
        content = "Product: F5OS-A\nVersion: 1\nRelease: 7\nBuild: 12345\nPlatform: R2R1X\n"
        _, version, build, platform = _parse_f5os_product_file(content)
        assert version == "1.7"
        assert build == "12345"
        assert platform == "R2R1X"


class TestPrefixPriorityAndCommandFiltering:
    def test_system_manager_outranks_partition(self):
        sys_mgr = "qkview/subpackages/system_manager/qkview"
        partition = "qkview/subpackages/partition1_manager/qkview"
        assert _f5os_prefix_priority(sys_mgr) < _f5os_prefix_priority(partition)

    def test_unknown_subpackage_lowest_priority(self):
        unknown = "qkview/subpackages/some_random_thing/qkview"
        sys_mgr = "qkview/subpackages/system_manager/qkview"
        assert _f5os_prefix_priority(unknown) > _f5os_prefix_priority(sys_mgr)

    def test_normalize_strips_confd_wrapper(self):
        wrapped = "/confd/scripts/f5_confd_run_cmd show running-config"
        assert _normalize_f5os_command_name(wrapped) == "show running-config"

    def test_quick_link_match(self):
        assert _is_quick_link_command(
            "/confd/scripts/f5_confd_run_cmd show system state"
        )
        assert _is_quick_link_command("show running-config")

    def test_quick_link_excludes_raw_license(self):
        # We deliberately drop raw-license dumps — they're huge and
        # contain no human-readable signal.
        assert not _is_quick_link_command("show system licensing raw-license")
        assert not _is_quick_link_command("show system licensing feature-flags")

    def test_quick_link_rejects_unrelated_command(self):
        assert not _is_quick_link_command("show whatever else")
