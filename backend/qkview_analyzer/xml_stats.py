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

from dataclasses import dataclass, field, asdict
from typing import IO, Iterable, Optional

from lxml import etree  # type: ignore


# ── record shapes ─────────────────────────────────────────────────────────


@dataclass
class StatRecord:
    """Generic key/value stat record extracted from an <object> block."""
    category: str
    fields: dict[str, str] = field(default_factory=dict)


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

    def top_virtual_servers(self, n: int = 20) -> list[StatRecord]:
        """Return the N busiest virtual servers by current connection count."""
        def _key(r: StatRecord) -> int:
            try:
                return int(r.fields.get("clientside.cur_conns", "0") or "0")
            except ValueError:
                return 0
        return sorted(self.virtual_servers, key=_key, reverse=True)[:n]

    def top_pools(self, n: int = 20) -> list[StatRecord]:
        """Return the N busiest pools by total connection count."""
        def _key(r: StatRecord) -> int:
            try:
                return int(r.fields.get("serverside.tot_conns", "0") or "0")
            except ValueError:
                return 0
        return sorted(self.pools, key=_key, reverse=True)[:n]

    def top_pool_members(self, n: int = 30) -> list[StatRecord]:
        """Return the N busiest pool members by total connection count.

        `pool_member_stat` rows have no explicit up/down state — that lives
        in the TMOS config's monitor rules. Traffic volume is the best
        operational-health proxy available from XML stats alone.
        """
        def _key(r: StatRecord) -> int:
            try:
                return int(r.fields.get("serverside.tot_conns", "0") or "0")
            except ValueError:
                return 0
        return sorted(self.pool_members, key=_key, reverse=True)[:n]

    def interfaces_with_errors(self) -> list[StatRecord]:
        """Return interfaces carrying any non-zero error/drop/collision counter."""
        def _has_errors(r: StatRecord) -> bool:
            for key in ("errors_in", "errors_out", "drops_in", "drops_out", "collisions"):
                try:
                    if int(r.fields.get(f"counters.{key}", "0") or "0") > 0:
                        return True
                except ValueError:
                    continue
            return False
        return [r for r in self.interfaces if _has_errors(r)]

    def top_expiring_certificates(self, n: int = 50) -> list[StatRecord]:
        """Return the N certificates with the soonest expiration date.

        `expiration_date` is a unix epoch seconds value in `certificate_summary`.
        Records with unparseable / missing dates sort last so valid certs
        always rank first.
        """
        def _key(r: StatRecord) -> int:
            raw = r.fields.get("expiration_date", "")
            try:
                return int(raw) if raw else 2**63 - 1
            except ValueError:
                return 2**63 - 1
        return sorted(self.certificates, key=_key)[:n]

    def summary(self) -> dict[str, int]:
        return {
            "virtual_servers": len(self.virtual_servers),
            "pools": len(self.pools),
            "pool_members": len(self.pool_members),
            "tmms": len(self.tmms),
            "interfaces": len(self.interfaces),
            "cpus": len(self.cpus),
            "db_variables": len(self.db_variables),
            "certificates": len(self.certificates),
            "active_modules": len(self.active_modules),
            "asm_policies": len(self.asm_policies),
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
