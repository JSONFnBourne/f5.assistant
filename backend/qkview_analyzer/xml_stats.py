"""Streaming parser for TMOS qkview *_module.xml runtime-stat files.

TMOS qkviews ship a handful of large (multi-MB) XML "module" dumps at the
archive root — `stat_module.xml`, `mcp_module.xml`, `chassis_module.xml` —
that hold runtime statistics, DB variables, certificate inventory, and
hardware identifiers. They are too big to load whole, so this module uses
lxml.iterparse to stream records out as dataclass instances.

Portions of the category taxonomy below are derived from f5-corkscrew's
`src/xmlStats.ts` (Apache License 2.0, Copyright 2014-2025 F5 Networks, Inc.).
See the top-level NOTICE file for full attribution. Modifications:
re-implemented in Python with streaming lxml.iterparse so the parser stays
memory-bounded on the 20+ MB XML payloads found in real qkviews.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import IO, Callable, Iterable, Optional

from lxml import etree  # type: ignore


# Bundle-index suffix: TMOS explodes each ca-bundle.crt file into one
# `certificate_summary` record per trusted CA, named `.../<bundle>.crt.NNN`.
# First-class user-imported / device certs are named without that numeric
# tail, so a trailing `.crt.<digits>` is a reliable signal that a cert row
# is trust-store material and not a user resource.
_BUNDLE_INDEX_RE = re.compile(r"\.crt\.\d+$")


# ── record shapes ─────────────────────────────────────────────────────────


@dataclass
class StatRecord:
    """Generic key/value stat record extracted from an <object> block."""
    category: str
    fields: dict[str, str] = field(default_factory=dict)


# Counter fields worth summing when collapsing replica rows for a single
# named resource. Anything not listed (vs_index, destination, plane_name,
# averaged-ratio fields, etc.) is kept from the first-seen replica because
# it's either an identifier or already aggregated by TMOS.
_VS_COUNTER_KEYS: tuple[str, ...] = (
    "clientside.cur_conns", "clientside.tot_conns", "clientside.max_conns",
    "clientside.pkts_in", "clientside.pkts_out",
    "clientside.bytes_in", "clientside.bytes_out",
    "serverside.cur_conns", "serverside.tot_conns", "serverside.max_conns",
    "serverside.pkts_in", "serverside.pkts_out",
    "serverside.bytes_in", "serverside.bytes_out",
    "no_nodes_errors",
)

_POOL_COUNTER_KEYS: tuple[str, ...] = (
    "serverside.cur_conns", "serverside.tot_conns", "serverside.max_conns",
    "serverside.pkts_in", "serverside.pkts_out",
    "serverside.bytes_in", "serverside.bytes_out",
    "cur_sessions", "tot_requests",
)

_MEMBER_COUNTER_KEYS: tuple[str, ...] = (
    "serverside.cur_conns", "serverside.tot_conns", "serverside.max_conns",
    "serverside.pkts_in", "serverside.pkts_out",
    "serverside.bytes_in", "serverside.bytes_out",
    "cur_sessions", "tot_requests",
)

_TMM_COUNTER_KEYS: tuple[str, ...] = (
    "client_side_traffic.pkts_in", "client_side_traffic.pkts_out",
    "client_side_traffic.bytes_in", "client_side_traffic.bytes_out",
    "client_side_traffic.tot_conns", "client_side_traffic.cur_conns",
    "client_side_traffic.max_conns",
    "server_side_traffic.pkts_in", "server_side_traffic.pkts_out",
    "server_side_traffic.bytes_in", "server_side_traffic.bytes_out",
    "server_side_traffic.tot_conns", "server_side_traffic.cur_conns",
    "server_side_traffic.max_conns",
)

_INTERFACE_COUNTER_KEYS: tuple[str, ...] = (
    "counters.pkts_in", "counters.pkts_out",
    "counters.bytes_in", "counters.bytes_out",
    "counters.errors_in", "counters.errors_out",
    "counters.drops_in", "counters.drops_out",
    "counters.collisions",
)


def _safe_int(v: Optional[str]) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _aggregate_by_key(
    records: Iterable[StatRecord],
    key_fn: Callable[[StatRecord], Optional[object]],
    counter_keys: tuple[str, ...],
) -> list[StatRecord]:
    """Collapse replica rows that share a dedupe key.

    Counter fields are summed across replicas. Non-counter fields are taken
    from the first-seen record — TMOS emits identical vs_index / destination
    / plane_name on every replica of the same resource, so picking "first"
    is equivalent to picking "any".

    Rows whose `key_fn` returns None are dropped. That's how callers filter
    out unnamed aggregate rows and internal-only resources.
    """
    groups: dict[object, StatRecord] = {}
    order: list[object] = []
    for r in records:
        key = key_fn(r)
        if key is None:
            continue
        existing = groups.get(key)
        if existing is None:
            groups[key] = StatRecord(category=r.category, fields=dict(r.fields))
            order.append(key)
            continue
        for ck in counter_keys:
            if ck in r.fields or ck in existing.fields:
                existing.fields[ck] = str(
                    _safe_int(existing.fields.get(ck)) + _safe_int(r.fields.get(ck))
                )
    return [groups[k] for k in order]


def _partitioned_name(r: StatRecord) -> Optional[str]:
    """Return `fields['name']` iff it names a user-visible partitioned
    resource (starts with `/`). Internal TMOS constructs like `_kmd_pool`,
    `_tmm_*`, `snat_automap[0]` and unnamed aggregate/wildcard rows return
    None so callers can drop them from counts and top-N tables.
    """
    name = (r.fields.get("name") or "").strip()
    return name if name.startswith("/") else None


def _non_empty_name(r: StatRecord) -> Optional[str]:
    """Return `fields['name']` if non-empty. Used for resources whose names
    aren't partitioned paths — TMM rows (`row0`..`rowN`), interfaces
    (`1.1`, `mgmt`). Empty-name aggregate / wildcard rows are filtered out.
    """
    name = (r.fields.get("name") or "").strip()
    return name or None


def _cpu_key(r: StatRecord) -> Optional[tuple[str, str, str]]:
    """Dedupe CPU rows by `(plane_name, cpu_id, slot_id)`.

    `cpu_info_stat` (TMM planes) and `system_cpu_info_stat` (control plane)
    both feed into `cpus`. Each cpu-core/plane combination gets sampled
    multiple times per capture; collapse to the first sample per tuple.
    Rows missing both cpu_id and slot_id are aggregate/global and get
    dropped.
    """
    cpu_id = (r.fields.get("cpu_id") or "").strip()
    slot = (r.fields.get("slot_id") or "").strip()
    if not cpu_id and not slot:
        return None
    plane = (r.fields.get("plane_name") or "").strip()
    return (plane, cpu_id, slot)


@dataclass
class XmlStats:
    """Collected runtime stats from a TMOS qkview."""
    virtual_servers: list[StatRecord] = field(default_factory=list)
    pools: list[StatRecord] = field(default_factory=list)
    pool_members: list[StatRecord] = field(default_factory=list)
    tmms: list[StatRecord] = field(default_factory=list)
    interfaces: list[StatRecord] = field(default_factory=list)
    cpus: list[StatRecord] = field(default_factory=list)
    db_variables: list[StatRecord] = field(default_factory=list)
    certificates: list[StatRecord] = field(default_factory=list)
    active_modules: list[StatRecord] = field(default_factory=list)
    asm_policies: list[StatRecord] = field(default_factory=list)
    other: list[StatRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: [asdict(r) for r in v] for k, v in asdict(self).items()}

    # ── deduped views ──────────────────────────────────────────────────
    # TMOS emits N replica rows per resource in stat_module.xml (one per
    # TMM / plane / sampling window depending on category). The raw lists
    # on this dataclass preserve every replica for callers that need the
    # unaggregated data (rule engine, future per-TMM breakdowns). These
    # methods produce the single-row-per-resource views the webapp and
    # iHealth both show.

    def deduped_virtual_servers(self) -> list[StatRecord]:
        return _aggregate_by_key(self.virtual_servers, _partitioned_name, _VS_COUNTER_KEYS)

    def deduped_pools(self) -> list[StatRecord]:
        return _aggregate_by_key(self.pools, _partitioned_name, _POOL_COUNTER_KEYS)

    def deduped_pool_members(self) -> list[StatRecord]:
        # Members are keyed by (parent pool, addr, port): the same backend
        # node can legitimately appear under two pools as distinct members.
        def _key(r: StatRecord) -> Optional[tuple[str, str, str]]:
            name = _partitioned_name(r)
            if name is None:
                return None
            return (name, r.fields.get("addr") or "", r.fields.get("port") or "")
        return _aggregate_by_key(self.pool_members, _key, _MEMBER_COUNTER_KEYS)

    def deduped_tmms(self) -> list[StatRecord]:
        return _aggregate_by_key(self.tmms, _non_empty_name, _TMM_COUNTER_KEYS)

    def deduped_interfaces(self) -> list[StatRecord]:
        return _aggregate_by_key(self.interfaces, _non_empty_name, _INTERFACE_COUNTER_KEYS)

    def deduped_cpus(self) -> list[StatRecord]:
        # CPU samples are averages, not counters — sorting by one_min_avg
        # on the first-seen replica gives the same answer as summing, and
        # summing averages would be semantically wrong. Pass an empty
        # tuple so _aggregate_by_key preserves first-seen values.
        return _aggregate_by_key(self.cpus, _cpu_key, ())

    def user_certificates(self) -> list[StatRecord]:
        """Certificates minus the trust-store bundle constituents.

        TMOS ships two public-CA trust bundles (`ca-bundle.crt`,
        `f5-ca-bundle.crt`) containing 900+ browser-trust CAs, each
        exported as an individual `certificate_summary` row named
        `.../ca-bundle.crt.NNN` or `.../f5-ca-bundle.crt.NNN`. These
        aren't operationally interesting (rotating them is an F5
        platform-update concern, not a deployment concern) and they
        drown the user-imported / device certs in the expiry panel.

        Any name ending in `.crt.<digits>` is a bundle index. First-class
        user-imported certs are named without the numeric tail.
        """
        return [r for r in self.certificates
                if not _BUNDLE_INDEX_RE.search(r.fields.get("name") or "")]

    # ── top-N tables ───────────────────────────────────────────────────

    def top_virtual_servers(self, n: int = 20) -> list[StatRecord]:
        """Return the N busiest virtual servers by current connection count."""
        def _key(r: StatRecord) -> int:
            return _safe_int(r.fields.get("clientside.cur_conns"))
        return sorted(self.deduped_virtual_servers(), key=_key, reverse=True)[:n]

    def top_pools(self, n: int = 20) -> list[StatRecord]:
        """Return the N busiest pools by total connection count."""
        def _key(r: StatRecord) -> int:
            return _safe_int(r.fields.get("serverside.tot_conns"))
        return sorted(self.deduped_pools(), key=_key, reverse=True)[:n]

    def top_pool_members(self, n: int = 30) -> list[StatRecord]:
        """Return the N busiest pool members by total connection count.

        `pool_member_stat` rows have no explicit up/down state — that lives
        in the TMOS config's monitor rules. Traffic volume is the best
        operational-health proxy available from XML stats alone.
        """
        def _key(r: StatRecord) -> int:
            return _safe_int(r.fields.get("serverside.tot_conns"))
        return sorted(self.deduped_pool_members(), key=_key, reverse=True)[:n]

    def interfaces_with_errors(self) -> list[StatRecord]:
        """Return interfaces carrying any non-zero error/drop/collision counter."""
        def _has_errors(r: StatRecord) -> bool:
            for key in ("errors_in", "errors_out", "drops_in", "drops_out", "collisions"):
                if _safe_int(r.fields.get(f"counters.{key}")) > 0:
                    return True
            return False
        return [r for r in self.deduped_interfaces() if _has_errors(r)]

    def top_expiring_certificates(self, n: int = 50) -> list[StatRecord]:
        """Return the N user/device certificates with the soonest expiration.

        Trust-store bundle entries (`/Common/ca-bundle.crt.NNN`) are
        excluded. `expiration_date` is a unix epoch seconds value;
        records with unparseable / missing dates sort last so valid
        certs always rank first.
        """
        def _key(r: StatRecord) -> int:
            raw = r.fields.get("expiration_date", "")
            try:
                return int(raw) if raw else 2**63 - 1
            except ValueError:
                return 2**63 - 1
        return sorted(self.user_certificates(), key=_key)[:n]

    def summary(self) -> dict[str, int]:
        """Distinct-resource counts matching what iHealth and TMOS CLI
        `tmsh list …` report. Each category dedupes its raw rows (see
        deduped_* methods) before the count so replica noise doesn't
        inflate the number.
        """
        return {
            "virtual_servers": len(self.deduped_virtual_servers()),
            "pools":           len(self.deduped_pools()),
            "pool_members":    len(self.deduped_pool_members()),
            "tmms":            len(self.deduped_tmms()),
            "interfaces":      len(self.deduped_interfaces()),
            "cpus":            len(self.deduped_cpus()),
            "db_variables":    len(self.db_variables),
            "certificates":    len(self.user_certificates()),
            "active_modules":  len(self.active_modules),
            "asm_policies":    len(self.asm_policies),
        }


# Map category tag → attribute on XmlStats. Anything not listed falls into
# `other` only if explicitly whitelisted via `_EXTRA_CATEGORIES` below, to
# avoid keeping thousands of unrelated stat rows in memory.
_CATEGORY_MAP = {
    "virtual_server_stat": "virtual_servers",
    "pool_stat": "pools",
    "pool_member_stat": "pool_members",
    "tmm_stat": "tmms",
    "interface_stat": "interfaces",
    "cpu_info_stat": "cpus",
    "system_cpu_info_stat": "cpus",
    "db_variable": "db_variables",
    # `certificate_summary` is the real top-level mcp_module.xml record; the
    # `certificate_list_stat` / `certificate_stat` names come from corkscrew
    # and don't appear in any TMOS qkview we've inspected, but they are left
    # in place in case a future TMOS release re-introduces them.
    "certificate_summary": "certificates",
    "certificate_list_stat": "certificates",
    "certificate_stat": "certificates",
    "active_modules": "active_modules",
    "asm_policy_stat": "asm_policies",
}

# Categories we pass through into XmlStats.other for ad-hoc inspection.
_EXTRA_CATEGORIES: set[str] = {
    "host_info_stat",
    "proc_stat",
    "plane_cpu_stat",
}


# ── streaming parsers ─────────────────────────────────────────────────────


# Keys we want to appear first in the rendered dict when present.
# F5 XML <object> elements carry the identifying name as an attribute
# (e.g. <object name="1.1">), not a child element — so without this the
# UI gets a dict starting with "if_index" / "counters.*" and never shows
# the interface or VS name.
_PRIORITY_KEYS = ("name", "obj_name", "status", "admin_state", "addr", "port")


def _read_object_fields(elem) -> dict[str, str]:
    """Collect leaf text values under an <object> element into a flat dict.

    Element attributes (notably `name="..."` on `<object>`) are folded in
    first so dict-iteration order in the UI naturally puts identifying
    fields before runtime counters.
    """
    out: dict[str, str] = {}
    for attr_name, attr_val in elem.attrib.items():
        out[attr_name] = attr_val
    for child in elem.iterchildren():
        if len(child) == 0:
            text = (child.text or "").strip()
            out[child.tag] = text
        else:
            # Nested <column><value>…</value></column> pairs appear in a
            # handful of categories — flatten them onto keys like "column.value".
            for grand in child.iterchildren():
                text = (grand.text or "").strip()
                out[f"{child.tag}.{grand.tag}"] = text

    # Promote known identifier keys to the front so the webapp displays a
    # human-readable label before numeric indexes/counters.
    promoted = {k: out[k] for k in _PRIORITY_KEYS if k in out}
    if promoted:
        rest = {k: v for k, v in out.items() if k not in promoted}
        out = {**promoted, **rest}
    return out


def parse_module_xml(stream: IO[bytes], stats: XmlStats) -> None:
    """Stream one *_module.xml file into `stats`.

    Two record shapes appear in the wild:
      (a) <category><object name="row0">…</object><object name="row1">…</object></category>
          — used for stat_module.xml runtime counters.
      (b) <db_variable>…fields…</db_variable><db_variable>…</db_variable>
          — used for mcp_module.xml DB variable dumps.

    We iterate on `end` events for every `<object>` and for every known
    direct-record tag, clearing each processed subtree to keep peak memory
    bounded.
    """
    context = etree.iterparse(
        stream,
        events=("end",),
        recover=True,
        huge_tree=True,
    )
    direct_tags = set(_CATEGORY_MAP.keys())

    for _, elem in context:
        tag = elem.tag
        category: Optional[str] = None

        if tag == "object":
            parent = elem.getparent()
            if parent is not None:
                category = parent.tag
        elif tag in direct_tags:
            category = tag
        else:
            continue

        if category is not None:
            record = StatRecord(category=category, fields=_read_object_fields(elem))
            attr = _CATEGORY_MAP.get(category)
            if attr is not None:
                getattr(stats, attr).append(record)
            elif category in _EXTRA_CATEGORIES:
                stats.other.append(record)

        elem.clear()
        prev = elem.getprevious()
        parent = elem.getparent()
        while prev is not None and parent is not None:
            del parent[0]
            prev = elem.getprevious()


def parse_xml_modules(files: Iterable[tuple[str, IO[bytes]]]) -> XmlStats:
    """Parse a sequence of (filename, stream) pairs into a single XmlStats."""
    stats = XmlStats()
    for _name, stream in files:
        try:
            parse_module_xml(stream, stats)
        except etree.XMLSyntaxError:
            # Some qkviews contain truncated XML tails; recover and keep what
            # we parsed before the break.
            continue
    return stats


def parse_xml_modules_from_tar(tar, filenames: Optional[list[str]] = None) -> XmlStats:
    """Convenience wrapper: pull the named *_module.xml members out of an open
    tarfile and stream-parse them all into a single XmlStats.

    Defaults to the three modules that carry useful data:
      stat_module.xml, mcp_module.xml, chassis_module.xml
    """
    names = filenames or ["stat_module.xml", "mcp_module.xml", "chassis_module.xml"]
    stats = XmlStats()
    for name in names:
        try:
            member = tar.getmember(name)
        except KeyError:
            continue
        f = tar.extractfile(member)
        if f is None:
            continue
        try:
            parse_module_xml(f, stats)
        except etree.XMLSyntaxError:
            continue
    return stats
