"""Parse F5 BIG-IP configuration files for VS/pool/member context."""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PoolMember:
    """A pool member (node:port)."""
    address: str
    port: int
    name: str = ""
    monitor_status: str = ""

    def __str__(self):
        return f"{self.address}:{self.port}"


@dataclass
class Pool:
    """An LTM pool."""
    name: str
    partition: str = "Common"
    members: list[PoolMember] = field(default_factory=list)
    monitor: str = ""
    lb_method: str = ""

    @property
    def full_path(self):
        return f"/{self.partition}/{self.name}"


@dataclass
class VirtualServer:
    """An LTM Virtual Server."""
    name: str
    partition: str = "Common"
    destination: str = ""
    ip_address: str = ""
    port: int = 0
    pool: str = ""
    profiles: list[str] = field(default_factory=list)
    irules: list[str] = field(default_factory=list)
    snat: str = ""
    source_address_translation: str = ""
    persist: str = ""
    description: str = ""

    @property
    def full_path(self):
        return f"/{self.partition}/{self.name}"


@dataclass
class SelfIP:
    """A network self IP."""
    name: str
    address: str = ""
    vlan: str = ""
    partition: str = "Common"


@dataclass
class VLAN:
    """A network VLAN."""
    name: str
    tag: int = 0
    partition: str = "Common"
    interfaces: list[str] = field(default_factory=list)


@dataclass
class BigIPConfig:
    """Parsed BIG-IP configuration."""
    hostname: str = ""
    virtual_servers: dict[str, VirtualServer] = field(default_factory=dict)
    pools: dict[str, Pool] = field(default_factory=dict)
    self_ips: dict[str, SelfIP] = field(default_factory=dict)
    vlans: dict[str, VLAN] = field(default_factory=dict)

    def find_pool_for_member(self, address: str) -> list[Pool]:
        """Find all pools containing a member with the given address."""
        results = []
        for pool in self.pools.values():
            for member in pool.members:
                if member.address == address or address in str(member):
                    results.append(pool)
                    break
        return results

    def find_vs_for_pool(self, pool_name: str) -> list[VirtualServer]:
        """Find all virtual servers referencing the given pool."""
        results = []
        for vs in self.virtual_servers.values():
            # Match full path or just name
            if vs.pool and (vs.pool == pool_name or vs.pool.endswith(f"/{pool_name}")):
                results.append(vs)
        return results

    def get_object_chain(self, member_address: str) -> list[dict]:
        """Get full chain: member -> pool -> VS for a given member address."""
        chains = []
        pools = self.find_pool_for_member(member_address)
        for pool in pools:
            vss = self.find_vs_for_pool(pool.full_path)
            for vs in vss:
                chains.append({
                    "member": member_address,
                    "pool": pool.full_path,
                    "pool_monitor": pool.monitor,
                    "pool_lb_method": pool.lb_method,
                    "virtual_server": vs.full_path,
                    "vs_destination": vs.destination,
                    "vs_irules": vs.irules,
                    "vs_profiles": vs.profiles,
                })
            if not vss:
                chains.append({
                    "member": member_address,
                    "pool": pool.full_path,
                    "pool_monitor": pool.monitor,
                    "pool_lb_method": pool.lb_method,
                    "virtual_server": "(none)",
                    "vs_destination": "",
                    "vs_irules": [],
                    "vs_profiles": [],
                })
        return chains


def _extract_stanza_blocks(content: str, object_type: str) -> list[tuple[str, str]]:
    """Extract named configuration blocks of a given type.

    Returns list of (full_name, block_content) tuples.
    Pattern: ltm virtual /Common/name { ... }
    """
    results = []
    # Regex to find the start of an object stanza
    pattern = re.compile(
        rf"^{re.escape(object_type)}\s+(/\S+)\s*\{{",
        re.MULTILINE,
    )

    for match in pattern.finditer(content):
        name = match.group(1)
        start = match.end()
        # Find matching closing brace (handle nesting)
        depth = 1
        pos = start
        while pos < len(content) and depth > 0:
            if content[pos] == "{":
                depth += 1
            elif content[pos] == "}":
                depth -= 1
            pos += 1
        block = content[start:pos - 1]
        results.append((name, block))

    return results


def _extract_value(block: str, key: str) -> str:
    """Extract a simple key-value from a config block."""
    pattern = re.compile(rf"^\s*{re.escape(key)}\s+(.+?)$", re.MULTILINE)
    match = pattern.search(block)
    if match:
        return match.group(1).strip()
    return ""


def _extract_list(block: str, key: str) -> list[str]:
    """Extract a list/block value (e.g., rules { ... })."""
    pattern = re.compile(rf"{re.escape(key)}\s*\{{([^}}]*)\}}", re.DOTALL)
    match = pattern.search(block)
    if match:
        items = match.group(1).strip().splitlines()
        return [item.strip() for item in items if item.strip()]
    return []


def _parse_pool_members(block: str) -> list[PoolMember]:
    """Parse members block from a pool definition.

    Handles the nested brace structure:
        members {
            /Common/10.10.20.20:3000 {
                address 10.10.20.20
            }
        }
    """
    # Find the members block using depth-aware brace matching
    members_start = block.find("members {")
    if members_start == -1:
        members_start = block.find("members{")
    if members_start == -1:
        return []

    # Find the opening brace of members
    brace_pos = block.index("{", members_start)
    depth = 1
    pos = brace_pos + 1
    while pos < len(block) and depth > 0:
        if block[pos] == "{":
            depth += 1
        elif block[pos] == "}":
            depth -= 1
        pos += 1
    members_block = block[brace_pos + 1:pos - 1]

    members = []
    # Each member: /Common/addr:port { address X.X.X.X }
    member_pattern = re.compile(r"(/\S+)\s*\{", re.DOTALL)
    for m in member_pattern.finditer(members_block):
        full_name = m.group(1)
        # Parse address:port from the last segment of the name
        name_part = full_name.split("/")[-1]  # e.g., "10.10.20.20:3000"
        if ":" in name_part:
            addr, port_str = name_part.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 0
        else:
            addr = name_part
            port = 0

        members.append(PoolMember(address=addr, port=port, name=full_name))

    return members


def parse_bigip_conf(content: str) -> BigIPConfig:
    """Parse a bigip.conf file into structured config objects."""
    config = BigIPConfig()

    # Parse virtual servers
    for name, block in _extract_stanza_blocks(content, "ltm virtual"):
        partition = name.split("/")[1] if "/" in name else "Common"
        vs_name = name.split("/")[-1]

        vs = VirtualServer(name=vs_name, partition=partition)
        vs.destination = _extract_value(block, "destination")
        vs.pool = _extract_value(block, "pool")
        vs.description = _extract_value(block, "description")
        vs.snat = _extract_value(block, "source-address-translation")
        vs.persist = _extract_value(block, "persist")
        vs.irules = _extract_list(block, "rules")
        vs.profiles = _extract_list(block, "profiles")

        # Parse IP from destination
        if vs.destination:
            dest = vs.destination.split("/")[-1]
            if ":" in dest:
                vs.ip_address, port_str = dest.rsplit(":", 1)
                try:
                    vs.port = int(port_str)
                except ValueError:
                    vs.port = 0

        config.virtual_servers[name] = vs

    # Parse pools
    for name, block in _extract_stanza_blocks(content, "ltm pool"):
        partition = name.split("/")[1] if "/" in name else "Common"
        pool_name = name.split("/")[-1]

        pool = Pool(name=pool_name, partition=partition)
        pool.monitor = _extract_value(block, "monitor")
        pool.lb_method = _extract_value(block, "load-balancing-mode")
        pool.members = _parse_pool_members(block)

        config.pools[name] = pool

    return config


def parse_bigip_base_conf(content: str) -> BigIPConfig:
    """Parse bigip_base.conf for network objects (VLANs, Self-IPs)."""
    config = BigIPConfig()

    # Parse VLANs
    for name, block in _extract_stanza_blocks(content, "net vlan"):
        partition = name.split("/")[1] if "/" in name else "Common"
        vlan_name = name.split("/")[-1]

        vlan = VLAN(name=vlan_name, partition=partition)
        tag = _extract_value(block, "tag")
        if tag:
            try:
                vlan.tag = int(tag)
            except ValueError:
                pass

        config.vlans[name] = vlan

    # Parse Self-IPs
    for name, block in _extract_stanza_blocks(content, "net self"):
        partition = name.split("/")[1] if "/" in name else "Common"
        self_name = name.split("/")[-1]

        self_ip = SelfIP(name=self_name, partition=partition)
        self_ip.address = _extract_value(block, "address")
        self_ip.vlan = _extract_value(block, "vlan")

        config.self_ips[name] = self_ip

    # Parse Hostname from sys global-settings
    hn_match = re.search(r'sys global-settings\s*\{[^}]*hostname\s+(\S+)', content, re.IGNORECASE)
    if hn_match:
        config.hostname = hn_match.group(1).strip()

    return config
