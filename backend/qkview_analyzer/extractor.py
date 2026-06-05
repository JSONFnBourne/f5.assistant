"""Extract and decompress files from F5 qkview tar.gz archives.

Supports both traditional BIG-IP (.qkview / .tgz) and F5OS rSeries/Velos (.tar) archives.
F5OS archives use a containerized subpackage structure with manifest.json metadata.

F5OS archives are stream-extracted to a temp directory in a single forward pass
before analysis: random-access reads on a gzipped tar pay a decompress-from-start
seek per member, so 130+ manifest reads on a VELOS archive cost ~minute. After
extraction, all F5OS analysis is plain filesystem reads on the tempdir.
"""

import gzip
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .xml_stats import XmlStats, parse_xml_modules_from_tar

logger = logging.getLogger("f5_backend")


@dataclass
class DeviceMeta:
    """Device metadata extracted from qkview."""
    product: str = ""
    version: str = ""
    build: str = ""
    edition: str = ""
    platform: str = ""
    hostname: str = ""
    cores: int = 0
    memory_mb: int = 0
    base_mac: str = ""
    # ISO 8601 UTC. For TMOS this is the max tar-member mtime (qkview write
    # time); for F5OS the root manifest's ts.start is authoritative and is
    # exposed separately on F5OSOverview.
    generation_date: str = ""


@dataclass
class F5OSHealthEntry:
    """A single F5OS system health finding."""
    component: str = ""
    health: str = ""        # "ok", "unhealthy"
    severity: str = ""      # "info", "error", "critical"
    attribute: str = ""
    description: str = ""
    value: str = ""
    updated_at: str = ""


@dataclass
class F5OSClusterNode:
    """One node from `show cluster` — rSeries has one, VELOS can have many."""
    name: str = ""
    running_state: str = ""   # "running", "initializing", ...
    ready: bool = False
    ready_message: str = ""
    slot: str = ""


@dataclass
class F5OSPortgroup:
    """One row of iHealth's "Portgroup Modes in Use"."""
    id: str = ""              # "1", "2", ...
    mode: str = ""             # "MODE_100GB", "MODE_25GB", ...


@dataclass
class F5OSTenant:
    """One tenant from `show tenants`."""
    name: str = ""
    type: str = ""             # "BIG-IP"
    running_state: str = ""    # "configured" | "provisioned" | "deployed"
    status: str = ""            # "Running" | ...
    image_version: str = ""
    mgmt_ip: str = ""
    vcpu_cores_per_node: str = ""
    memory_mb: str = ""


@dataclass
class F5OSOverview:
    """iHealth-style overview card set for F5OS archives.

    Fields mirror the rSeries iHealth dashboard so the webapp can render
    the familiar "Generation Date / Platform / System Status / Configuration
    Totals" layout verbatim. Populated from the root ``qkview/manifest.json``
    plus a handful of ``show …`` command outputs already captured in
    ``QKViewData.f5os_commands``.
    """
    # Top strip (iHealth breadcrumb bar)
    generation_start: str = ""         # ts.start from root manifest, ISO 8601
    generation_stop: str = ""          # ts.stop from root manifest
    platform_pid: str = ""             # "C129" — iHealth's Platform parenthetical
    platform_code: str = ""            # "R5R10" — PRODUCT/platform_info.platform
    platform_part_number: str = ""
    platform_uuid: str = ""
    platform_slot: str = ""
    version_edition: str = ""          # "1.8.3-23453" from product_info.version

    # System Status card
    cluster_summary: str = ""           # derived one-liner
    cluster_nodes: list[F5OSClusterNode] = field(default_factory=list)
    mgmt_ipv4_address: str = ""
    mgmt_ipv4_prefix: str = ""
    mgmt_ipv4_gateway: str = ""
    mgmt_ipv6_address: str = ""
    mgmt_ipv6_prefix: str = ""
    mgmt_ipv6_gateway: str = ""
    payg_license_level: str = ""        # "r5800" from licensing
    licensed_version: str = ""
    registration_key: str = ""
    licensed_date: str = ""
    serial_number: str = ""
    time_zone: str = ""
    appliance_datetime: str = ""        # current appliance clock at collection

    # Configuration Totals card
    appliance_mode: str = ""            # "enabled" | "disabled"
    portgroups: list[F5OSPortgroup] = field(default_factory=list)
    tenants: list[F5OSTenant] = field(default_factory=list)
    tenants_configured: int = 0
    tenants_provisioned: int = 0
    tenants_deployed: int = 0
    tenants_running: int = 0


@dataclass
class QKViewData:
    """All extracted data from a qkview archive."""
    meta: DeviceMeta = field(default_factory=DeviceMeta)
    log_files: dict[str, str] = field(default_factory=dict)       # name -> content
    config_files: dict[str, str] = field(default_factory=dict)     # name -> content
    tmstat_files: dict[str, bytes] = field(default_factory=dict)   # name -> binary
    raw_meta_files: dict[str, str] = field(default_factory=dict)   # VERSION.LTM, etc.
    f5os_health: list[F5OSHealthEntry] = field(default_factory=list)
    # F5OS event log content (raw text) for separate parsing
    f5os_event_log: str = ""
    f5os_system_events: str = ""
    # F5OS manifest-driven CLI command outputs (iHealth Quick Links style).
    # Key is the trimmed command name ("show running-config"), value is the
    # captured output. Populated from every discovered subpackage manifest.
    f5os_commands: dict[str, str] = field(default_factory=dict)
    # Structured iHealth-style overview assembled from root manifest +
    # `show system mgmt-ip` / `show system clock` / `show cluster` etc.
    f5os_overview: Optional["F5OSOverview"] = None
    # Per-partition / per-tenant extra diag dumps from TMOS var/tmp.
    diag_files: dict[str, str] = field(default_factory=dict)
    # Streaming-parsed runtime stats from TMOS *_module.xml payloads (TMOS only).
    xml_stats: Optional[XmlStats] = None


# Log files we care about — everything under var/log/
# Config files for context. Per-partition configs under config/partitions/*/
# are picked up dynamically, not listed here.
_CONFIG_FILES = [
    "config/bigip.conf",
    "config/bigip_base.conf",
    "config/bigip_gtm.conf",
    "config/bigip_user.conf",
    "config/user_alert.conf",
    "config/.cluster.conf",
    "config/cipher.conf",
    "config/profile_base.conf",
    "config/low_profile_base.conf",
    "config/snmp/subagents.conf",
    "config/snmp/bigipTrafficMgmt.conf",
]

# TMOS daemon/runtime dumps captured into data.diag_files. Any file under
# var/tmp/ matching these suffixes is kept as text.
_DIAG_PATTERNS = (".out", "_dump.txt", "_dump.log", ".diag")

# Metadata files to dynamically hunt for
_META_FILES_BASENAMES = {
    "VERSION.LTM",
    "PLATFORM",
    "HWINFO",
    "VERSION",
}

# Decompression safety limits
_MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024   # 8 GB total extracted
_MAX_SINGLE_FILE_BYTES  = 512 * 1024 * 1024          # 512 MB per file

# Log file prefixes to skip (binary or not useful for text analysis)
_SKIP_PREFIXES = [
    "var/log/journal/",      # systemd binary journals
    "var/log/wtmp",          # binary login records
    "var/log/btmp",          # binary failed login records
    "var/log/lastlog",       # binary last login
    "var/log/pam/tallylog",  # binary tally
    "var/log/bootchart/",    # compressed bootchart
]


def _should_skip_log(name: str) -> bool:
    """Check if a log file should be skipped (binary/non-text)."""
    return any(name.startswith(prefix) for prefix in _SKIP_PREFIXES)


def _read_member_text(tar: tarfile.TarFile, member: tarfile.TarInfo) -> Optional[str]:
    """Read a tar member as text, handling _transformed (gzip) files."""
    f = tar.extractfile(member)
    if f is None:
        return None

    raw = f.read()

    # _transformed files are gzip-compressed rotated logs
    if member.name.endswith("_transformed"):
        try:
            raw = gzip.decompress(raw)
        except (gzip.BadGzipFile, OSError):
            # Not actually gzip, try as-is
            pass

    # Try to decode as text
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def _parse_version_ltm(content: str) -> dict[str, str]:
    """Parse VERSION.LTM key-value format."""
    result = {}
    for line in content.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip().lower()] = value.strip()
    return result


def _parse_keyval(content: str) -> dict[str, str]:
    """Parse key=value format (PLATFORM, HWINFO)."""
    result = {}
    for line in content.strip().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _build_device_meta(raw_meta: dict[str, str]) -> DeviceMeta:
    """Build DeviceMeta from raw metadata file contents."""
    meta = DeviceMeta()

    # TMOS VERSION.LTM
    if "VERSION.LTM" in raw_meta:
        v = _parse_version_ltm(raw_meta["VERSION.LTM"])
        meta.product = v.get("product", "")
        meta.version = v.get("version", "")
        meta.build = v.get("build", "")
        meta.edition = v.get("edition", "")
    # F5OS VERSION (YAML-like key: value)
    elif "VERSION" in raw_meta and "product" not in meta.product:
        v = _parse_version_ltm(raw_meta["VERSION"]) # uses same parser as LTM basically
        meta.product = "F5OS"
        meta.version = v.get("version", "")
        meta.platform = v.get("platform", "")

    # Parse PLATFORM
    if "PLATFORM" in raw_meta:
        p = _parse_keyval(raw_meta["PLATFORM"])
        # Only override if missing, PLATFORM file sometimes generic
        if not meta.platform:
            meta.platform = p.get("platform", "")

    # Parse HWINFO
    if "HWINFO" in raw_meta:
        h = _parse_keyval(raw_meta["HWINFO"])
        meta.base_mac = h.get("basemac", "")
        try:
            meta.cores = int(h.get("cores", "0"))
        except ValueError:
            meta.cores = 0
        try:
            mem_kb = int(h.get("memory-size", "0"))
            meta.memory_mb = mem_kb // 1024
        except ValueError:
            meta.memory_mb = 0

    return meta


# ─── F5OS streaming extraction ────────────────────────────────────────────


def _is_f5os_archive_streaming(qkview_path: Path) -> bool:
    """Detect F5OS by spotting a `qkview/subpackages/...` member at the front.

    The root `qkview/manifest.json` is often appended at the *end* of F5OS
    archives (rSeries host puts subpackage outputs first, manifest last),
    so relying on it for fast detection means bailing wrongly on every
    such archive. The `qkview/subpackages/` directory hierarchy, on the
    other hand, dominates the first members of every F5OS variant we've
    seen and never appears in TMOS .qkview / .tgz archives — so a single
    matching member at the front is a reliable signal. Bounded look-ahead
    keeps the classifier fast even on huge TMOS archives.
    """
    try:
        with tarfile.open(str(qkview_path), "r|*") as tar:
            for i, member in enumerate(tar):
                name = member.name
                if name.startswith("qkview/subpackages/"):
                    return True
                if name == "qkview/manifest.json":
                    return True
                if i > 64:
                    return False
    except (tarfile.TarError, OSError):
        return False
    return False


def _f5os_should_extract(name: str) -> bool:
    """Decide whether to keep a tar member during F5OS stream extraction.

    The allowlist matches every path that ``_extract_f5os`` later reads off
    disk. Anything else is dropped to keep the tempdir small and the linear
    decompress fast — a VELOS syscon archive otherwise unpacks to ~3 GB,
    most of it irrelevant kubernetes/openshift filesystem snapshots.
    """
    # Kubernetes/openshift pod wrappers ship in VELOS syscon archives with
    # zero F5 diagnostic signal. They also pollute the analyzer's log index
    # with kubernetes log noise — drop them at the door.
    if "/subpackages/k8s_" in name:
        return False

    # Root manifest (single entry).
    if name == "qkview/manifest.json":
        return True

    if "/subpackages/" not in name:
        return False

    # Per-subpackage manifest is required for the discovery walk and
    # iHealth-Quick-Links command capture.
    if name.endswith("/manifest.json"):
        return True

    # PRODUCT / PRODUCT.LTS — authoritative running F5OS version + platform.
    if "/filesystem/etc/PRODUCT" in name:
        return True

    # Manifest-referenced command outputs live under commands/<hash>/<n>/out.
    if "/commands/" in name and name.endswith("/out"):
        return True

    # Memory + kernel banner.
    if name.endswith("/filesystem/proc/meminfo"):
        return True
    if name.endswith("/filesystem/version"):
        return True

    # Log file roots that the F5OS extractor reads from. The pattern list in
    # ``_extract_f5os`` is the source of truth for which specific files are
    # consumed; here we only need to keep the directories.
    if "/filesystem/var/log/" in name:
        # Skip systemd binary journals — same rule the TMOS branch applies.
        if "/var/log/journal/" in name:
            return False
        return True
    if "/filesystem/var/F5/" in name:
        return True
    if "/filesystem/var/log_controller/" in name:
        return True
    if "/filesystem/tmp/" in name and name.endswith(".log"):
        return True
    if name.endswith("/qkview-collect.log"):
        return True

    return False


def _extract_tempdir_root() -> Path:
    """Return the directory used to stage extracted F5OS qkviews.

    Defaults to ``/var/tmp`` because the standard ``tempfile.gettempdir()``
    on this host is a tmpfs that cannot hold a fully-extracted F5OS tree
    (a VELOS syscon archive unpacks to 2–3 GB even after the allowlist
    filter). Override with ``F5_QKVIEW_TMPDIR`` for tests / alternate
    deployments.
    """
    override = os.environ.get("F5_QKVIEW_TMPDIR")
    if override:
        return Path(override)
    return Path("/var/tmp")


def _stream_extract_f5os_to_dir(
    qkview_path: Path,
    dest_dir: Path,
    progress_callback=None,
) -> None:
    """Stream-extract relevant F5OS members in a single forward pass.

    Opens the archive in ``r|*`` mode (no random-access seeks). Each
    member is decided on as it streams past. The single-pass decompress is
    O(N) over the gzip stream; the previous random-access approach paid an
    O(N) seek per ``getmember`` call, which is what made manifest-walk
    discovery cost ~64 s on VELOS archives.
    """
    extracted = 0
    skipped = 0
    last_progress = 0
    with tarfile.open(str(qkview_path), "r|*") as tar:
        for member in tar:
            name = member.name

            # Path-traversal guard — same rule the TMOS branch enforces.
            if name.startswith("/") or ".." in name.split("/"):
                skipped += 1
                continue
            # Symlinks/hardlinks in F5OS subpackages point at host paths
            # outside the tempdir; never materialise them.
            if member.issym() or member.islnk():
                skipped += 1
                continue
            if not _f5os_should_extract(name):
                skipped += 1
                continue
            if member.size > _MAX_SINGLE_FILE_BYTES:
                logger.debug("skip oversize member %s (%d bytes)", name, member.size)
                skipped += 1
                continue

            try:
                tar.extract(member, path=str(dest_dir), set_attrs=False)
                extracted += 1
            except (tarfile.TarError, OSError) as e:
                logger.debug("skip extract %s: %s", name, e)
                skipped += 1
                continue

            if progress_callback and extracted - last_progress >= 250:
                last_progress = extracted
                progress_callback(f"Extracting F5OS members ({extracted} files)…")

    if progress_callback:
        progress_callback(
            f"F5OS extraction: kept {extracted} members, skipped {skipped}"
        )


def _read_text(path: Path) -> Optional[str]:
    """Read a file as UTF-8 text, with gzip auto-decompress for *_transformed."""
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return None

    if path.name.endswith("_transformed"):
        try:
            raw = gzip.decompress(raw)
        except (gzip.BadGzipFile, OSError):
            pass

    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


# ─── F5OS metadata + manifest helpers (filesystem-backed) ─────────────────


def _parse_f5os_manifest_commands(
    root: Path,
    subpackage_prefix: str,
    manifest_data: Optional[dict] = None,
) -> dict[str, str]:
    """Return a command_name -> output_path mapping for a subpackage.

    ``manifest_data`` is optional — if supplied, we trust the cached parse
    instead of re-reading. Output paths are returned root-relative
    (``qkview/.../out``) so callers can do ``_read_text(root / path)``.
    """
    if manifest_data is None:
        text = _read_text(root / subpackage_prefix / "manifest.json")
        if text is None:
            return {}
        try:
            manifest_data = json.loads(text)
        except json.JSONDecodeError:
            return {}

    commands: dict[str, str] = {}
    for cmd in manifest_data.get("commands", []):
        name = cmd.get("name", "")
        output = cmd.get("output", "")
        if name and output:
            commands[name] = f"{subpackage_prefix}/{output}"
    return commands


# Priority order for picking the "primary" metadata source across all discovered
# subpackages. Host-level appliances expose system_manager; partition qkviews use
# partition1_manager / partition_manager; VELOS syscon uses vcc-confd for chassis CLI.
_F5OS_MANAGER_PRIORITY = (
    "system_manager",
    "partition1_manager",
    "partition_manager",
    "vcc-confd",
    "appliance_orchestration_manager",
    "partition1_common",
)


def _discover_f5os_subpackage_prefixes(
    root: Path, root_data: dict
) -> tuple[list[str], dict[str, dict]]:
    """Recursively discover every subpackage qkview prefix under ``root``.

    Handles VELOS partition/syscon archives whose top-level packages list
    `peer-qkview.<ip>` wrappers — each wrapper carries its own manifest.json
    with a nested packages list. Walks the tree and returns every
    ``qkview/.../qkview`` prefix that has a manifest, plus a cache of the
    parsed manifest body keyed by prefix.
    """
    prefixes: list[str] = []
    manifests: dict[str, dict] = {}
    seen: set[str] = set()

    def _walk(base_prefix: str, manifest_data: dict, depth: int = 0) -> None:
        if depth > 4:
            return
        for pkg in manifest_data.get("packages", []):
            # Prefer the on-disk directory derived from `path` — on rSeries
            # and VELOS syscon archives the `host-qkview.tar.gz` subpackage
            # is listed under a display name ("appliance-1", "controller-2")
            # that doesn't match its on-disk directory. Using `name` would
            # silently skip host-qkview, which is where PRODUCT lives.
            path = pkg.get("path", "")
            dir_name = ""
            if path.startswith("subpackages/"):
                tail = path[len("subpackages/"):]
                for ext in (".tar.gz", ".tgz", ".tar"):
                    if tail.endswith(ext):
                        tail = tail[: -len(ext)]
                        break
                dir_name = tail
            if not dir_name:
                dir_name = pkg.get("name", "")
            if not dir_name:
                continue
            # Same k8s_* skip the stream extractor enforces — defensive: a
            # future change that re-admits k8s_* tar members still won't
            # index them as analysis subpackages.
            if dir_name.startswith("k8s_"):
                continue
            child_prefix = f"{base_prefix}/subpackages/{dir_name}/qkview"
            if child_prefix in seen:
                continue
            seen.add(child_prefix)
            prefixes.append(child_prefix)

            child_text = _read_text(root / child_prefix / "manifest.json")
            if not child_text:
                continue
            try:
                child_data = json.loads(child_text)
            except json.JSONDecodeError:
                continue
            manifests[child_prefix] = child_data
            _walk(child_prefix, child_data, depth + 1)

    manifests["qkview"] = root_data
    _walk("qkview", root_data)
    return prefixes, manifests


# iHealth Quick-Links-style commands we want to surface verbatim from any
# F5OS subpackage manifest. Matched as substring, so "show system" picks up
# the whole family. Names are the exact strings used in manifest.json.
#
# The union below covers:
#   * the iHealth Quick-Links panels for rSeries / VELOS partition / VELOS
#     syscon (see QKVIEW_FORMATS.md §iHealth Quick Links),
#   * the chassis/admin commands called out as gaps in QKVIEW_FORMATS.md
#     (show slots, show system chassis-macs, show system blade-power,
#     show ctrlr_status, show system aaa, show system appliance-mode).
#
# `_collect_f5os_quick_link_outputs` dedupes by command name, so peer-qkview
# wrappers don't multiply payload size — each command is captured once.
_F5OS_QUICK_LINK_COMMANDS = (
    # Core iHealth Quick-Links entries (union of rSeries / partition / syscon)
    "show running-config",
    "show cluster",
    "show components",
    "show interfaces",
    "show lacp",
    "show tenants",
    "show partitions",
    "show service",          # catches service-pods, services, service-instances, service-table
    # System state family
    "show system state",
    "show system version",
    "show system image",
    "show system licensing",
    "show system events",
    "show system alarms",
    "show system health",
    "show system redundancy",  # covers redundancy, redundancy-details, redundancy detail/fault/status
    "show system settings",
    "show system cpu",
    "show system memory",
    "show system processes",
    "show system uptime",
    "show system controller",
    "show system mgmt-ip",
    "show system clock",
    "show portgroups",
    # Platform / chassis detail (primarily VELOS syscon, some rSeries)
    "show slots",
    "show ctrlr_status",
    "show chassis",
    "show blades",
    "show images",            # includes "show image" on syscon
    "show system chassis-macs",
    "show system blade-power",
    "show system aaa",
    "show system appliance-mode",
    # Forensic / admin trail
    "show history",
    "show last-logins",
    "show vlans",
)

# Patterns we explicitly reject even when they substring-match the allowlist
# above — `raw-license` and `feature-flags` are duplicates of `show system
# licensing` and drag extra payload for no analysis value.
_F5OS_QUICK_LINK_DENYLIST = ("raw-license", "feature-flags")

# Per-command output cap. Individual F5OS command outputs are typically
# 2–50 KB; `show running-config` can reach a few hundred KB on a busy
# chassis. Cap at 512 KB so a pathological archive can't runaway the
# NDJSON payload (total payloads today are ~1.5 MB post trim — we want
# to preserve that budget).
_F5OS_QUICK_LINK_MAX_BYTES = 512 * 1024


def _normalize_f5os_command_name(name: str) -> str:
    """Strip the F5OS confd wrapper so keys read like `show running-config`."""
    stripped = name.strip()
    for prefix in ("/confd/scripts/f5_confd_run_cmd", "/usr/bin/env", "/bin/sh -c"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].strip()
    return stripped


def _is_quick_link_command(name: str) -> bool:
    lo = _normalize_f5os_command_name(name).lower()
    if any(token in lo for token in _F5OS_QUICK_LINK_DENYLIST):
        return False
    return any(key in lo for key in _F5OS_QUICK_LINK_COMMANDS)


def _f5os_prefix_priority(prefix: str) -> tuple[int, int]:
    """Sort key that ranks manager subpackages above everything else, and
    local subpackages above peer-qkview wrappers for the same manager.

    VELOS partition archives ship `partition_manager` both locally
    (`qkview/subpackages/partition_manager/`) and under every peer blade's
    wrapper (`qkview/subpackages/peer-qkview.<ip>/qkview/subpackages/partition_manager/`).
    The dedup in `_collect_f5os_quick_link_outputs` is first-match-wins, so
    without a local-vs-peer tiebreak a peer blade's command output could
    mask the partition's own — the local archive is always the authoritative
    one for the family-specific reporter fields (platform, hostname, config).
    """
    subpkg = prefix.rsplit("/subpackages/", 1)[-1].split("/", 1)[0]
    try:
        mgr_rank = _F5OS_MANAGER_PRIORITY.index(subpkg)
    except ValueError:
        mgr_rank = len(_F5OS_MANAGER_PRIORITY)
    peer_rank = 1 if "/peer-qkview." in prefix else 0
    return (mgr_rank, peer_rank)


def _pick_primary_f5os_prefix(
    root: Path,
    prefixes: list[str],
    manifests: Optional[dict[str, dict]] = None,
) -> tuple[str, dict[str, str]]:
    """Pick the first prefix whose manifest yields commands, preferring host
    managers over partition/chassis ones.

    Returns ``(prefix, commands_dict)``. If nothing matches, returns ``("", {})``.
    """
    manifests = manifests or {}
    for prefix in sorted(prefixes, key=_f5os_prefix_priority):
        cmds = _parse_f5os_manifest_commands(root, prefix, manifests.get(prefix))
        if cmds:
            return prefix, cmds
    return "", {}


def _collect_f5os_quick_link_outputs(
    root: Path,
    prefixes: list[str],
    manifests: Optional[dict[str, dict]] = None,
) -> dict[str, str]:
    """Walk every discovered manifest and return outputs for any command whose
    name matches the iHealth Quick-Links allowlist, keyed by trimmed name.

    Prefixes are visited in manager-priority order so that host-level
    subpackages fill the slot before partition / chassis agents.
    """
    manifests = manifests or {}
    out: dict[str, str] = {}
    for prefix in sorted(prefixes, key=_f5os_prefix_priority):
        cmds = _parse_f5os_manifest_commands(root, prefix, manifests.get(prefix))
        for name, path in cmds.items():
            if not _is_quick_link_command(name):
                continue
            key = _normalize_f5os_command_name(name)
            if key in out:
                continue
            content = _read_text(root / path)
            if not (content and content.strip()):
                continue
            if len(content) > _F5OS_QUICK_LINK_MAX_BYTES:
                content = (
                    content[:_F5OS_QUICK_LINK_MAX_BYTES]
                    + f"\n… [truncated at {_F5OS_QUICK_LINK_MAX_BYTES // 1024} KB]"
                )
            out[key] = content
    return out


def _parse_f5os_system_state(content: str) -> dict[str, str]:
    """Parse 'show system state' output for hostname, base-mac, etc.

    Example:
        system state hostname t9355-host-1301b-mgmt.target.com
        system state base-mac 14:a9:d0:49:68:00
        system state mac-pool-size 256
    """
    result = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("system state hostname "):
            result["hostname"] = line.split("system state hostname ", 1)[1].strip()
        elif line.startswith("system state base-mac "):
            result["base-mac"] = line.split("system state base-mac ", 1)[1].strip()
        elif line.startswith("system state current-datetime "):
            val = line.split("system state current-datetime ", 1)[1].strip().strip('"')
            result["datetime"] = val
    return result


def _parse_f5os_system_image(content: str) -> str:
    """Parse 'show system image' output to find the active (IN USE = true) version.

    Returns the version string of the active OS image.
    """
    # The output has columns: VERSION OS | STATUS | DATE | SIZE | IN USE | TYPE
    # We look for 'true' in the IN USE column
    lines = content.splitlines()
    for line in lines:
        # Skip headers, dashes, blank lines
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("VERSION"):
            continue

        parts = stripped.split()
        if not parts:
            continue

        # Look for 'true' in the parts (IN USE column)
        if "true" in parts:
            # First part should be the version
            version = parts[0]
            # Validate it looks like a version number
            if re.match(r"\d+\.\d+", version):
                return version

    return ""


def _parse_f5os_product_file(content: str) -> tuple[str, str, str, str]:
    """Parse a host-qkview PRODUCT* file for running F5OS version + platform.

    The file is written by F5OS at install and updated on upgrade, so it
    reflects the *active* OS — unlike `show system licensing`, which reports
    the last licensed version even after the running image has been
    upgraded. Example content::

        Product: F5OS-C
        Version: 1
        Release: 8
        Patch: 2
        Build: 28311
        Platform: controller
        Tag: LTS

    Returns ``(product, version_string, build, platform)``. Version is
    composed as ``<Version>.<Release>.<Patch>``.
    """
    fields: dict[str, str] = {}
    for line in content.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip().lower()] = v.strip()

    product = fields.get("product", "")
    version_parts = [fields[k] for k in ("version", "release", "patch") if k in fields]
    version = ".".join(version_parts)
    build = fields.get("build", "")
    platform = fields.get("platform", "")
    return product, version, build, platform


def _parse_f5os_licensing_version(content: str) -> str:
    """Parse 'show system licensing' output to extract the licensed F5OS version.

    Used as a fallback for partition-level qkviews that lack 'show system image'.
    Example line: '                         Licensed version    1.6.2'
    """
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("Licensed version"):
            parts = stripped.split()
            # Format: "Licensed version    <ver>"
            if len(parts) >= 3:
                version = parts[-1]
                if re.match(r"\d+\.\d+", version):
                    return version
    return ""


def _parse_f5os_meminfo(content: str) -> int:
    """Parse proc/meminfo to extract MemTotal in MB."""
    for line in content.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    kb = int(parts[1])
                    return kb // 1024
                except ValueError:
                    pass
    return 0


def _parse_f5os_system_health(content: str) -> list[F5OSHealthEntry]:
    """Parse 'show system health' output for unhealthy components.

    Extracts structured health findings from the tabular output, focusing on
    components that are unhealthy with error or critical severity.
    """
    findings = []
    lines = content.splitlines()

    current_component = ""
    in_detail_section = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("-") or stripped.startswith("COMPONENT") or stripped.startswith("ATTRIBUTE"):
            continue

        # Check for main component health summary lines
        # Format: <name>  -  <health>  <severity>  <attribute_name> ...
        if stripped.startswith("system health components component"):
            # Nested detail section
            in_detail_section = True
            continue

        if in_detail_section:
            # Skip indented detail sections for now
            if stripped.startswith("state ") or stripped.startswith("hardware "):
                continue

        # Parse the main health table lines
        # Lines with "unhealthy" and "error"/"critical" contain findings
        if "unhealthy" in stripped:
            parts = stripped.split()
            if len(parts) >= 3:
                # Find the component name (first non-dash field)
                comp = parts[0] if parts[0] != "-" else current_component
                if comp and comp != "-":
                    current_component = comp

                # Find attribute description by looking for quoted or descriptive text
                # The format varies, so we do a best-effort parse
                severity = ""
                description = ""

                for i, p in enumerate(parts):
                    if p in ("critical", "error", "warning"):
                        severity = p
                    elif p == "-" and i > 0:
                        continue

                # Extract the attribute description (long text between severity fields)
                # Look for patterns like "PSU Status Output OK" or "PSU Main IOUT..."
                text_parts = []
                found_first_health = False
                for p in parts:
                    if p in ("unhealthy", "ok"):
                        if found_first_health:
                            break
                        found_first_health = True
                        continue
                    if found_first_health and p not in ("critical", "error", "warning", "info", "-"):
                        text_parts.append(p)

                description = " ".join(text_parts).strip()
                if not description:
                    # Fallback: try to find quoted text or descriptive parts
                    desc_match = re.search(r'((?:PSU|CPU|SSD|ASW|ATSE|Firmware|Watchdog)[\w\s-]{1,100})', stripped)
                    if desc_match:
                        description = desc_match.group(1).strip()

                if description and severity:
                    findings.append(F5OSHealthEntry(
                        component=current_component,
                        health="unhealthy",
                        severity=severity,
                        description=description,
                    ))

    return findings


def _parse_f5os_mgmt_ip(content: str) -> dict[str, str]:
    """Parse `show system mgmt-ip` into a flat {kind: value} dict.

    kinds: ipv4_address, ipv4_prefix, ipv4_gateway, ipv6_address, ipv6_prefix,
    ipv6_gateway.
    """
    out: dict[str, str] = {}
    rules = (
        ("system mgmt-ip state ipv4 system address ", "ipv4_address"),
        ("system mgmt-ip state ipv4 prefix-length ",   "ipv4_prefix"),
        ("system mgmt-ip state ipv4 gateway ",         "ipv4_gateway"),
        ("system mgmt-ip state ipv6 system address ", "ipv6_address"),
        ("system mgmt-ip state ipv6 prefix-length ",   "ipv6_prefix"),
        ("system mgmt-ip state ipv6 gateway ",         "ipv6_gateway"),
    )
    for line in content.splitlines():
        s = line.strip()
        for prefix, key in rules:
            if s.startswith(prefix):
                out[key] = s[len(prefix):].strip()
                break
    return out


def _parse_f5os_clock(content: str) -> dict[str, str]:
    """Parse `show system clock` → timezone-name + appliance date-time."""
    out: dict[str, str] = {}
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("system clock state timezone-name "):
            out["timezone"] = s[len("system clock state timezone-name "):].strip()
        elif s.startswith("system clock state appliance date-time "):
            out["datetime"] = s[len("system clock state appliance date-time "):].strip().strip('"')
    return out


def _parse_f5os_appliance_mode(content: str) -> str:
    """Return 'enabled' | 'disabled' from `show system appliance-mode`."""
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("system appliance-mode state "):
            return s[len("system appliance-mode state "):].strip()
    return ""


def _parse_f5os_licensing(content: str) -> dict[str, str]:
    """Parse `show system licensing` for PAYG level, registration key, dates.

    The `Active Modules` block lists each module as e.g.
    ``Local Traffic Manager, r5800 (DTSJKCS-NLVTKRA)`` — we extract the
    platform tier (``r5800``) which iHealth surfaces as "PAYG License Level".
    """
    out: dict[str, str] = {}
    in_modules = False
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Licensed version"):
            parts = line.split()
            if len(parts) >= 3:
                out["licensed_version"] = parts[-1]
        elif line.startswith("Registration Key"):
            out["registration_key"] = line.split()[-1]
        elif line.startswith("Licensed date"):
            out["licensed_date"] = line.split()[-1]
        elif line.startswith("Active Modules"):
            in_modules = True
            continue
        elif line.startswith("Time Limited Modules"):
            in_modules = False

        if in_modules and "payg_license_level" not in out:
            # First "<Module>, <tier> (<key>)" row wins. Tier follows the
            # first comma; drop the trailing registration-key parenthetical.
            m = re.match(r"^[A-Za-z].*?,\s*([A-Za-z0-9_-]+)\s*\(", line)
            if m:
                out["payg_license_level"] = m.group(1)
    return out


def _parse_f5os_cluster(content: str) -> tuple[list[F5OSClusterNode], str]:
    """Parse `show cluster` into per-node state + a derived summary string.

    Returns (nodes, summary). Summary mimics iHealth's "K3S cluster is
    initialized and ready for use." when every node is ready; otherwise
    reports how many of N are ready.
    """
    nodes: list[F5OSClusterNode] = []
    current: Optional[F5OSClusterNode] = None
    for raw in content.splitlines():
        line = raw.rstrip()
        m = re.match(r"^cluster nodes node (\S+)\s*$", line)
        if m:
            current = F5OSClusterNode(name=m.group(1))
            nodes.append(current)
            continue
        # Guard against sibling-stanza bleed: `state …` rows are indented.
        if raw and not raw.startswith((" ", "\t")):
            current = None
            continue
        if current is None:
            continue
        s = line.strip()
        if s.startswith("state node-running-state "):
            current.running_state = s[len("state node-running-state "):].strip()
        elif s.startswith("state slot-number "):
            current.slot = s[len("state slot-number "):].strip()
        elif s.startswith("state ready-info ready "):
            current.ready = s[len("state ready-info ready "):].strip() == "true"
        elif s.startswith('state ready-info message "'):
            current.ready_message = s[len('state ready-info message "'):].rstrip('"').strip()

    if nodes:
        ready_count = sum(1 for n in nodes if n.ready)
        if ready_count == len(nodes):
            summary = "K3S cluster is initialized and ready for use."
        else:
            summary = f"{ready_count}/{len(nodes)} nodes ready"
    else:
        summary = ""
    return nodes, summary


def _parse_f5os_portgroup_modes(running_config: str) -> list[F5OSPortgroup]:
    """Pull portgroup id + mode out of `show running-config`.

    Expected stanza shape (one per portgroup)::

        portgroups portgroup 1
         config name 1
         config mode MODE_100GB
         ...

    Missing mode lines (unoccupied portgroups) are kept as empty strings so
    the UI can still show all physical portgroups and flag the gaps.
    """
    out: list[F5OSPortgroup] = []
    current: Optional[F5OSPortgroup] = None
    for raw in running_config.splitlines():
        m = re.match(r"^portgroups portgroup (\S+)\s*$", raw)
        if m:
            current = F5OSPortgroup(id=m.group(1))
            out.append(current)
            continue
        # Stanza rows are space-indented; any unindented line ends the
        # current portgroup. Without this guard a later sibling stanza
        # ("system … config mode standard") would overwrite portgroup N's
        # mode because the parser never clears `current`.
        if raw and not raw.startswith((" ", "\t")):
            current = None
            continue
        if current is None:
            continue
        s = raw.strip()
        if s.startswith("config mode "):
            current.mode = s[len("config mode "):].strip()
    return out


def _parse_f5os_tenants(content: str) -> list[F5OSTenant]:
    """Parse `show tenants` into a list of F5OSTenant rows.

    Tenants on F5OS appear as ``tenants tenant <name>`` stanzas followed by
    indented ``state …`` lines. The stanza terminates at the next blank line
    or at a new ``tenants tenant`` line; trailing per-pod tables (NODE/POD
    NAME) are ignored.
    """
    out: list[F5OSTenant] = []
    current: Optional[F5OSTenant] = None
    for raw in content.splitlines():
        m = re.match(r"^tenants tenant (\S+)\s*$", raw)
        if m:
            current = F5OSTenant(name=m.group(1))
            out.append(current)
            continue
        if raw and not raw.startswith((" ", "\t")):
            current = None
            continue
        if current is None:
            continue
        s = raw.strip()
        # F5OS pads between key and value with variable whitespace, so split
        # on the first whitespace run after the known field name.
        m2 = re.match(r"^state (\S+(?:-\S+)*)\s+(.+?)\s*$", s)
        if not m2:
            continue
        key, value = m2.group(1), m2.group(2).strip().strip('"')
        if key == "type":
            current.type = value
        elif key == "running-state":
            current.running_state = value
        elif key == "status":
            current.status = value
        elif key == "image-version":
            current.image_version = value
        elif key == "mgmt-ip":
            current.mgmt_ip = value
        elif key == "vcpu-cores-per-node":
            current.vcpu_cores_per_node = value
        elif key == "memory":
            current.memory_mb = value
    return out


def _build_f5os_overview(
    root_manifest: dict,
    commands: dict[str, str],
) -> F5OSOverview:
    """Assemble the iHealth-style overview from the root manifest and the
    already-captured ``show …`` outputs.

    ``commands`` is keyed by trimmed command name (``"show system clock"``
    etc.), matching ``QKViewData.f5os_commands``. Missing commands leave the
    corresponding fields empty — this runs on partial archives too.
    """
    ov = F5OSOverview()

    ts = root_manifest.get("ts") or {}
    if isinstance(ts, dict):
        ov.generation_start = ts.get("start", "") or ""
        ov.generation_stop = ts.get("stop", "") or ""

    pi = root_manifest.get("platform_info") or {}
    if isinstance(pi, dict):
        ov.platform_pid = pi.get("pid", "") or ""
        ov.platform_code = pi.get("platform_type", "") or pi.get("platform", "") or ""
        ov.platform_part_number = pi.get("part_number", "") or ""
        ov.platform_uuid = pi.get("uuid", "") or ""
        ov.platform_slot = str(pi.get("slot", "") or "")
        ov.serial_number = pi.get("serial_number", "") or ""

    prod = root_manifest.get("product_info") or {}
    if isinstance(prod, dict):
        ov.version_edition = prod.get("version", "") or ""

    mgmt = commands.get("show system mgmt-ip")
    if mgmt:
        m = _parse_f5os_mgmt_ip(mgmt)
        ov.mgmt_ipv4_address = m.get("ipv4_address", "")
        ov.mgmt_ipv4_prefix = m.get("ipv4_prefix", "")
        ov.mgmt_ipv4_gateway = m.get("ipv4_gateway", "")
        ov.mgmt_ipv6_address = m.get("ipv6_address", "")
        ov.mgmt_ipv6_prefix = m.get("ipv6_prefix", "")
        ov.mgmt_ipv6_gateway = m.get("ipv6_gateway", "")

    clock = commands.get("show system clock")
    if clock:
        c = _parse_f5os_clock(clock)
        ov.time_zone = c.get("timezone", "")
        ov.appliance_datetime = c.get("datetime", "")

    appl = commands.get("show system appliance-mode")
    if appl:
        ov.appliance_mode = _parse_f5os_appliance_mode(appl)

    lic = commands.get("show system licensing")
    if lic:
        li = _parse_f5os_licensing(lic)
        ov.payg_license_level = li.get("payg_license_level", "")
        ov.licensed_version = li.get("licensed_version", "")
        ov.registration_key = li.get("registration_key", "")
        ov.licensed_date = li.get("licensed_date", "")

    cl = commands.get("show cluster")
    if cl:
        ov.cluster_nodes, ov.cluster_summary = _parse_f5os_cluster(cl)

    rc = commands.get("show running-config")
    if rc:
        ov.portgroups = _parse_f5os_portgroup_modes(rc)

    tn = commands.get("show tenants")
    if tn:
        ov.tenants = _parse_f5os_tenants(tn)
        # iHealth tallies by running-state. "Running" is a status, not a
        # running-state — it's derived from status == "Running".
        for t in ov.tenants:
            rs = (t.running_state or "").lower()
            if rs == "configured":
                ov.tenants_configured += 1
            elif rs == "provisioned":
                ov.tenants_provisioned += 1
            elif rs == "deployed":
                ov.tenants_deployed += 1
            if (t.status or "").lower() == "running":
                ov.tenants_running += 1

    return ov


def _extract_f5os_platform_from_events(content: str) -> str:
    """Try to extract platform model from event log reboot messages.

    Example: 'reboot - appliance-1.chassis.local F5OS-A R5R10 version 1.5.0-5781'
    """
    match = re.search(r"F5OS-[AC]\s+(\w+)\s+version", content)
    if match:
        return match.group(1)
    return ""


def _extract_f5os(root: Path, progress_callback=None) -> QKViewData:
    """Analyze an already-extracted F5OS qkview tree under ``root``.

    ``root`` is the directory that contains ``qkview/manifest.json`` (i.e.
    the parent of the ``qkview`` directory). Always invoked via
    ``_extract_f5os_via_tempdir`` so that ``root`` is a freshly populated
    temp directory.
    """
    data = QKViewData()
    data.meta.product = "F5OS"

    if progress_callback:
        progress_callback("Parsing F5OS subpackage structure…")

    root_text = _read_text(root / "qkview" / "manifest.json")
    if not root_text:
        return data
    try:
        root_data = json.loads(root_text)
    except json.JSONDecodeError:
        return data

    # ── 1. Discover all subpackage prefixes (handles VELOS peer-qkview wrappers) ──
    all_prefixes, manifest_cache = _discover_f5os_subpackage_prefixes(root, root_data)
    sys_mgr_prefix, sys_mgr_cmds = _pick_primary_f5os_prefix(root, all_prefixes, manifest_cache)

    # Capture every iHealth Quick-Link-style command output across all
    # subpackages so the webapp can render them without re-opening anything.
    data.f5os_commands = _collect_f5os_quick_link_outputs(root, all_prefixes, manifest_cache)

    if progress_callback:
        progress_callback("Extracting F5OS system metadata…")

    # show system state → hostname, base-mac
    for cmd_name, cmd_path in sys_mgr_cmds.items():
        if "show system state" in cmd_name:
            content = _read_text(root / cmd_path)
            if content:
                state = _parse_f5os_system_state(content)
                data.meta.hostname = state.get("hostname", "")
                data.meta.base_mac = state.get("base-mac", "")
            break

    # PRODUCT file → authoritative running F5OS version + platform.
    # Written by the installer and updated on every upgrade, so it reflects
    # the currently booted image across every F5OS qkview variant
    # (rSeries host, VELOS partition, VELOS syscon). This is our primary
    # version source; `show system image` / `show system licensing` are only
    # used when no PRODUCT file can be located (extremely rare).
    #
    # Prefer local prefixes over peer-qkview.* wrappers so a partition
    # archive reports its own ``controller`` platform rather than one of the
    # peer blades.
    product_by_prefix: dict[str, Path] = {}
    for prefix in all_prefixes:
        etc_dir = root / prefix / "filesystem" / "etc"
        if not etc_dir.is_dir():
            continue
        try:
            for entry in etc_dir.iterdir():
                if not entry.is_file():
                    continue
                name = entry.name
                if name == "PRODUCT" or name.startswith("PRODUCT."):
                    product_by_prefix[prefix] = entry
                    break
        except OSError:
            continue

    local_prefixes = [p for p in all_prefixes if "/peer-qkview." not in p]
    peer_prefixes = [p for p in all_prefixes if "/peer-qkview." in p]
    for prefix in local_prefixes + peer_prefixes:
        candidate = product_by_prefix.get(prefix)
        if candidate is None:
            continue
        content = _read_text(candidate)
        if not content:
            continue
        product, version, build, platform = _parse_f5os_product_file(content)
        if version:
            data.meta.version = version
            if build:
                data.meta.build = build
            if platform:
                data.meta.platform = platform
            if product:
                data.meta.product = product
            break

    # show system image → version fallback if no PRODUCT file was found.
    if not data.meta.version:
        for cmd_name, cmd_path in sys_mgr_cmds.items():
            if "show system image" in cmd_name:
                content = _read_text(root / cmd_path)
                if content:
                    data.meta.version = _parse_f5os_system_image(content)
                break

    # show system licensing → last-resort version fallback.
    if not data.meta.version:
        for cmd_name, cmd_path in sys_mgr_cmds.items():
            if "show system licensing" in cmd_name and "raw-license" not in cmd_name and "feature-flags" not in cmd_name:
                content = _read_text(root / cmd_path)
                if content:
                    data.meta.version = _parse_f5os_licensing_version(content)
                break

    # show system events → event log entries
    for cmd_name, cmd_path in sys_mgr_cmds.items():
        if "show system events" in cmd_name:
            content = _read_text(root / cmd_path)
            if content:
                data.f5os_system_events = content
                # Try to extract platform from reboot events
                if not data.meta.platform:
                    data.meta.platform = _extract_f5os_platform_from_events(content)
            break

    # show system health → health findings
    for cmd_name, cmd_path in sys_mgr_cmds.items():
        if "show system health" in cmd_name:
            content = _read_text(root / cmd_path)
            if content:
                data.f5os_health = _parse_f5os_system_health(content)
            break

    # show running-config → F5OS config
    for cmd_name, cmd_path in sys_mgr_cmds.items():
        if "show running-config" in cmd_name:
            content = _read_text(root / cmd_path)
            if content:
                data.config_files["f5os/running-config"] = content
            break

    # ── 2. Extract memory from proc/meminfo ──────────────────────────
    # Look in any discovered subpackage — host-level managers (system_manager,
    # appliance_orchestration_manager) and partition managers all carry meminfo.
    for prefix in all_prefixes:
        content = _read_text(root / prefix / "filesystem" / "proc" / "meminfo")
        if content:
            data.meta.memory_mb = _parse_f5os_meminfo(content)
            break

    # ── 3. Collect log files from every discovered subpackage (includes
    # VELOS peer-qkview.<ip>/.../<subpkg> nested prefixes).
    if progress_callback:
        progress_callback("Collecting F5OS log files from subpackages…")

    log_count = 0

    # Known log file patterns within subpackages. F5OS scatters logs across
    # host-qkview/filesystem/, filesystem/var/log_controller/ (VELOS syscon),
    # and filesystem/var/F5/{system,partition}/log/.
    _LOG_FILE_PATTERNS = [
        "filesystem/var/log/lopd/lopd.log",
        "filesystem/var/log/lopd/run_lopd.log",
        "filesystem/var/log/platform/pel",
        "filesystem/var/log/openshift.log",
        "filesystem/var/log/optics-mgr.log",
        "filesystem/var/log_controller/velos.log",
        "filesystem/var/log_controller/pel_log.log",
        "filesystem/var/log_controller/openshift.log",
        "filesystem/var/F5/system/log/platform.log",
        "filesystem/var/F5/partition/log/velos.log",
        "filesystem/var/F5/partition/log/optics-mgr.log",
        "filesystem/var/F5/partition/events/event-log.log",
        "filesystem/var/F5/partition/log/tcam_dump-ipv4_src.txt",
        "filesystem/var/F5/partition/log/tcam_dump-ipv4_dst.txt",
        "filesystem/var/F5/partition/log/tcam_dump-ipv6_src.txt",
        "filesystem/var/F5/partition/log/tcam_dump-ipv6_dst.txt",
        "filesystem/var/F5/partition/log/reg_dump_main.txt",
        "filesystem/var/F5/partition/log/reg_dump_mc.txt",
        "filesystem/tmp/license_qkview.log",
        "filesystem/tmp/oa_qkview.log",
        "qkview-collect.log",
    ]

    for prefix in all_prefixes:
        # Build a short label: prefer the last subpackage name, but prepend
        # peer IP when present so VELOS peer logs stay distinguishable.
        label_parts = []
        peer_match = re.search(r"peer-qkview\.([^/]+)", prefix)
        if peer_match:
            label_parts.append(f"peer-{peer_match.group(1)}")
        subpkg_name = prefix.rsplit("/subpackages/", 1)[-1].split("/", 1)[0]
        label_parts.append(subpkg_name)
        label = "/".join(label_parts)

        for pattern in _LOG_FILE_PATTERNS:
            content = _read_text(root / prefix / pattern)
            if not (content and content.strip()):
                continue

            if pattern == "qkview-collect.log":
                log_name = f"qkview-collect/{label}"
            elif "var/log/" in pattern:
                log_name = f"{label}/{pattern[pattern.find('var/log/') + len('var/log/'):]}"
            elif "var/F5/" in pattern:
                log_name = f"{label}/{pattern[pattern.find('var/F5/'):]}"
            else:
                log_name = f"{label}/{pattern.split('/')[-1]}"

            if log_name not in data.log_files:
                data.log_files[log_name] = content
            else:
                data.log_files[log_name] += "\n" + content
            log_count += 1

            if "event-log.log" in pattern and not data.f5os_event_log:
                data.f5os_event_log = content

    # ── 4. Extract platform from VERSION files if still unknown ──────
    if not data.meta.platform:
        for prefix in all_prefixes[:8]:
            content = _read_text(root / prefix / "filesystem" / "version")
            if content:
                data.raw_meta_files["VERSION"] = content
                break

    # ── 5. Assemble the iHealth-style overview from root manifest +
    # already-captured quick-link command outputs.
    try:
        data.f5os_overview = _build_f5os_overview(root_data, data.f5os_commands)
    except Exception:
        logger.exception("F5OS overview assembly failed; continuing without it")
        data.f5os_overview = None

    if data.f5os_overview and data.f5os_overview.generation_start:
        data.meta.generation_date = data.f5os_overview.generation_start

    if progress_callback:
        progress_callback(
            f"F5OS extraction complete: {log_count} log files, "
            f"hostname={data.meta.hostname}, version={data.meta.version}"
        )

    return data


def _extract_f5os_via_tempdir(
    qkview_path: Path,
    progress_callback=None,
) -> QKViewData:
    """Stream-extract the F5OS archive to a tempdir, run analysis, clean up."""
    tmp_root = _extract_tempdir_root()
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="f5os_qkview_", dir=str(tmp_root)))
    try:
        if progress_callback:
            progress_callback(f"Stream-extracting F5OS archive to {tmp_dir}…")
        _stream_extract_f5os_to_dir(qkview_path, tmp_dir, progress_callback)
        return _extract_f5os(tmp_dir, progress_callback)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── Traditional BIG-IP extraction ────────────────────────────────────────


def _extract_tmos(qkview_path: Path, progress_callback=None) -> QKViewData:
    """Extract data from a traditional TMOS .qkview / .tgz archive."""
    with tarfile.open(str(qkview_path), "r:*") as tar:
        members = tar.getmembers()
        total = len(members)

        # Zip bomb / excessive member guard
        if total > 500_000:
            raise ValueError(f"Archive contains {total} members — refusing to process (limit: 500,000)")

        # Path traversal guard; symlinks/hardlinks are skipped (not extracted to disk)
        # TMOS qkviews legitimately contain symlinks (e.g. VERSION -> VERSION.LTM,
        # usr/share/monitors/*) — rejecting them would break all BIG-IP archives.
        for member in members:
            if member.name.startswith('/') or '..' in member.name.split('/'):
                raise ValueError(f"Unsafe archive member path detected: {member.name!r}")

            # Per-file size guard (member.size is the uncompressed size reported in the header)
            # Symlinks report size 0 — skip the guard for them to avoid false positives.
            if not (member.issym() or member.islnk()) and member.size > _MAX_SINGLE_FILE_BYTES:
                raise ValueError(
                    f"Archive member {member.name!r} reports size {member.size:,} bytes "
                    f"(limit: {_MAX_SINGLE_FILE_BYTES:,})"
                )

        data = QKViewData()
        total_decompressed = 0
        max_mtime = 0

        for i, member in enumerate(members):
            if not member.isfile():
                continue

            name = member.name

            # Track max file mtime — qkview writes all collected files at
            # generation time, so the newest mtime is the generation date.
            if member.mtime and member.mtime > max_mtime:
                max_mtime = member.mtime

            # Running decompressed-size guard (uses header-reported size)
            total_decompressed += member.size
            if total_decompressed > _MAX_DECOMPRESSED_BYTES:
                raise ValueError(
                    f"Total decompressed size exceeded {_MAX_DECOMPRESSED_BYTES:,} bytes — "
                    "aborting to prevent decompression bomb"
                )

            # Progress update every 100 files
            if progress_callback and i % 100 == 0:
                progress_callback(f"Processing {i}/{total}: {name}")

            # Metadata files (target strict basenames to handle F5OS nested variations)
            basename = name.split("/")[-1]
            if basename in _META_FILES_BASENAMES:
                content = _read_member_text(tar, member)
                if content:
                    data.raw_meta_files[basename] = content
                continue

            # Log files (dynamically seek var/log/ to bypass F5OS subpackage wrappers)
            if "var/log/" in name:
                if _should_skip_log(name):
                    continue
                content = _read_member_text(tar, member)
                if content and content.strip():
                    log_name = name[name.find("var/log/") + len("var/log/"):]

                    if "partitions/" in name:
                        part_id = name.split("partitions/")[1].split("/")[0]
                        log_name = f"partition_{part_id}/{log_name}"
                    elif "host/var/log/" in name:
                        log_name = f"host/{log_name}"

                    if log_name not in data.log_files:
                        data.log_files[log_name] = content
                    else:
                        data.log_files[log_name] += "\n" + content
                continue

            # Config files
            if name in _CONFIG_FILES:
                content = _read_member_text(tar, member)
                if content:
                    data.config_files[name] = content
                continue

            # Per-partition bigip.conf / bigip_base.conf dumps.
            # Structure: config/partitions/<name>/bigip.conf
            if name.startswith("config/partitions/") and name.endswith(".conf"):
                content = _read_member_text(tar, member)
                if content:
                    data.config_files[name] = content
                continue

            # Daemon dumps & diagnostic text under var/tmp/. Skip huge rollups
            # like storage_text_dump.txt (often 50MB+) that aren't useful for
            # summary analysis.
            if name.startswith("var/tmp/") and name.endswith(_DIAG_PATTERNS):
                if member.size > 2 * 1024 * 1024:
                    continue
                content = _read_member_text(tar, member)
                if content and content.strip():
                    diag_name = name[len("var/tmp/"):]
                    data.diag_files[diag_name] = content
                continue

            # tmstat data (keep as binary for now). Two layouts coexist:
            #   - appliance snapshots under shared/tmstat/snapshots/...
            #   - live segments under var/tmstat/... (TMOS VE; also the F5OS
            #     subpackage path .../filesystem/var/tmstat/...)
            # Match both so VE/F5OS tmstat members aren't silently dropped.
            if "shared/tmstat/" in name or "var/tmstat/" in name:
                f = tar.extractfile(member)
                if f:
                    data.tmstat_files[name] = f.read()
                continue

    # Build device metadata
    data.meta = _build_device_meta(data.raw_meta_files)
    if max_mtime:
        from datetime import datetime, timezone
        data.meta.generation_date = (
            datetime.fromtimestamp(max_mtime, tz=timezone.utc).isoformat()
        )

    # Hostname lives in `sys global-settings { hostname foo.bar }` inside
    # config/bigip_base.conf on TMOS. Pulled out here so the overview header
    # and System Status panel don't show a blank.
    base_conf = data.config_files.get("config/bigip_base.conf", "")
    if base_conf and not data.meta.hostname:
        m = re.search(r"^\s*hostname\s+(\S+)", base_conf, re.MULTILINE)
        if m:
            data.meta.hostname = m.group(1)

    # Stream-parse the big *_module.xml payloads for runtime stats. Done in a
    # second tar pass because the first pass consumes file pointers.
    if progress_callback:
        progress_callback("Parsing runtime stats from *_module.xml...")
    try:
        with tarfile.open(str(qkview_path), "r:*") as xml_tar:
            data.xml_stats = parse_xml_modules_from_tar(xml_tar)
    except (tarfile.TarError, OSError):
        data.xml_stats = None

    if progress_callback:
        summary = data.xml_stats.summary() if data.xml_stats else {}
        progress_callback(
            f"Extraction complete: {len(data.log_files)} log files, "
            f"{len(data.config_files)} config files, "
            f"{summary.get('virtual_servers', 0)} VIPs, "
            f"{summary.get('db_variables', 0)} db_variables"
        )

    return data


def extract_qkview(qkview_path: str | Path, progress_callback=None) -> QKViewData:
    """Extract all relevant data from a qkview archive.

    Dispatches to the F5OS or TMOS pipeline based on the presence of a
    root ``qkview/manifest.json``. F5OS archives are stream-extracted to a
    tempdir for fast subsequent reads; TMOS archives are processed directly
    out of the gzipped tar (members are accessed sequentially during a
    single ``getmembers`` walk, which is already cheap).
    """
    qkview_path = Path(qkview_path)
    if not qkview_path.exists():
        raise FileNotFoundError(f"QKView file not found: {qkview_path}")

    if progress_callback:
        progress_callback("Opening qkview archive…")

    if _is_f5os_archive_streaming(qkview_path):
        if progress_callback:
            progress_callback("Detected F5OS archive — staging to tempdir…")
        return _extract_f5os_via_tempdir(qkview_path, progress_callback)

    return _extract_tmos(qkview_path, progress_callback)
