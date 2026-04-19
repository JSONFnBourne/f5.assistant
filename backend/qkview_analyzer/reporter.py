"""Rich terminal output and JSON export for qkview analysis results."""

import json
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich import box

from .extractor import DeviceMeta, QKViewData
from .rule_engine import Finding
from .config_parser import BigIPConfig


# Severity-to-color mapping (bash-like terminal coloring)
SEVERITY_COLORS = {
    "emerg": "bold white on red",
    "alert": "bold red",
    "crit": "bold red",
    "err": "red",
    "warning": "yellow",
    "notice": "cyan",
    "info": "green",
    "debug": "dim white",
}

FINDING_SEVERITY_COLORS = {
    "critical": "bold red",
    "warning": "yellow",
    "info": "cyan",
}


class Reporter:
    """Generate Rich terminal output and JSON exports."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()

    # ── Device Summary ──────────────────────────────────────────

    def print_device_summary(self, meta: DeviceMeta):
        """Print device information panel."""
        table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")

        table.add_row("Product", f"{meta.product} {meta.version}")
        table.add_row("Build", f"{meta.build} ({meta.edition})")
        table.add_row("Platform", meta.platform)
        table.add_row("Hostname", meta.hostname)
        table.add_row("Cores", str(meta.cores))
        table.add_row("Memory", f"{meta.memory_mb} MB")
        table.add_row("Base MAC", meta.base_mac)

        panel = Panel(
            table,
            title="[bold white]Device Summary[/]",
            border_style="blue",
            padding=(0, 1),
        )
        self.console.print(panel)

    # ── Log Statistics ──────────────────────────────────────────

    def print_log_stats(
        self,
        total_entries: int,
        time_range: tuple,
        severity_summary: dict,
        source_summary: dict,
        process_summary: dict,
    ):
        """Print log statistics panel."""
        # Time range
        start, end = time_range
        time_str = "Unknown"
        if start and end:
            time_str = f"{start.strftime('%Y-%m-%d %H:%M:%S')} → {end.strftime('%Y-%m-%d %H:%M:%S')}"

        self.console.print()
        self.console.print(
            Panel(
                f"[bold]{total_entries:,}[/] log entries  •  {time_str}",
                title="[bold white]Log Overview[/]",
                border_style="blue",
            )
        )

        # Severity table
        sev_table = Table(title="Severity Distribution", box=box.ROUNDED, show_lines=False)
        sev_table.add_column("Severity", style="bold")
        sev_table.add_column("Count", justify="right")
        sev_table.add_column("Bar")

        max_count = max(severity_summary.values()) if severity_summary else 1
        for sev, count in severity_summary.items():
            color = SEVERITY_COLORS.get(sev, "white")
            bar_len = int((count / max_count) * 30)
            bar = "█" * bar_len
            sev_table.add_row(
                Text(sev, style=color),
                str(count),
                Text(bar, style=color),
            )

        # Top processes table
        proc_table = Table(title="Top Processes", box=box.ROUNDED, show_lines=False)
        proc_table.add_column("Process", style="bold cyan")
        proc_table.add_column("Entries", justify="right")

        for proc, count in list(process_summary.items())[:10]:
            proc_table.add_row(proc, f"{count:,}")

        self.console.print()
        self.console.print(Columns([sev_table, proc_table], padding=2))

    # ── Known Issues / Findings ─────────────────────────────────

    def print_findings(self, findings: list[Finding]):
        """Print known-issue findings."""
        if not findings:
            self.console.print()
            self.console.print(
                Panel(
                    "[green]✓ No known issues detected[/]",
                    title="[bold white]Known Issue Scan[/]",
                    border_style="green",
                )
            )
            return

        self.console.print()
        self.console.print(
            Panel(
                f"[bold yellow]⚠ {len(findings)} known issue(s) detected[/]",
                title="[bold white]Known Issue Scan[/]",
                border_style="yellow",
            )
        )

        for finding in findings:
            color = FINDING_SEVERITY_COLORS.get(finding.severity, "white")
            severity_badge = Text(f" {finding.severity.upper()} ", style=f"bold white on {color.replace('bold ', '')}")

            # Finding header
            self.console.print()
            self.console.print(
                f"  {severity_badge} [bold]{finding.rule_description}[/]"
            )
            self.console.print(f"    [dim]Rule:[/] {finding.rule_name}  "
                             f"[dim]Category:[/] {finding.category}  "
                             f"[dim]Occurrences:[/] {finding.count}")

            if finding.first_seen and finding.last_seen:
                self.console.print(
                    f"    [dim]First:[/] {finding.first_seen.strftime('%Y-%m-%d %H:%M:%S')}  "
                    f"[dim]Last:[/] {finding.last_seen.strftime('%Y-%m-%d %H:%M:%S')}"
                )

            # Recommendation
            self.console.print(f"    [dim italic]→ {finding.recommendation.strip()}[/]")

            # Sample entries
            if finding.matched_entries:
                self.console.print(f"    [dim]Sample entries:[/]")
                for entry in finding.matched_entries[:3]:
                    sev = entry.get("severity", "info")
                    sev_color = SEVERITY_COLORS.get(sev, "white")
                    raw = entry.get("raw_line", "")
                    # Truncate long lines
                    if len(raw) > 120:
                        raw = raw[:117] + "..."
                    self.console.print(f"      [{sev_color}]{raw}[/]")

    # ── Log Entries ─────────────────────────────────────────────

    def print_log_entries(self, entries: list[dict], show_source: bool = True):
        """Print log entries with severity coloring."""
        for entry in entries:
            sev = entry.get("severity", "info")
            color = SEVERITY_COLORS.get(sev, "white")
            raw = entry.get("raw_line", "")

            if show_source:
                source = entry.get("source_file", "")
                self.console.print(f"[dim]{source}:[/] [{color}]{raw}[/]")
            else:
                self.console.print(f"[{color}]{raw}[/]")

    def print_log_entries_table(self, entries: list[dict]):
        """Print log entries in a structured table."""
        table = Table(box=box.SIMPLE, show_lines=False, padding=(0, 1))
        table.add_column("Time", style="dim", width=19)
        table.add_column("Sev", width=8)
        table.add_column("Process", style="cyan", width=15)
        table.add_column("Code", width=10)
        table.add_column("Message", no_wrap=False)

        for entry in entries:
            sev = entry.get("severity", "info")
            color = SEVERITY_COLORS.get(sev, "white")
            ts = entry.get("timestamp", "")[:19]
            msg = entry.get("message", "")
            if len(msg) > 100:
                msg = msg[:97] + "..."

            table.add_row(
                ts,
                Text(sev, style=color),
                entry.get("process", ""),
                entry.get("msg_code", "") or "",
                msg,
            )

        self.console.print(table)

    # ── Config Context ──────────────────────────────────────────

    def print_config_summary(self, config: BigIPConfig):
        """Print parsed configuration summary."""
        self.console.print()

        # Virtual Servers
        vs_table = Table(title="Virtual Servers", box=box.ROUNDED)
        vs_table.add_column("Name", style="bold cyan")
        vs_table.add_column("Destination")
        vs_table.add_column("Pool", style="yellow")
        vs_table.add_column("iRules")

        for name, vs in config.virtual_servers.items():
            irules = ", ".join(vs.irules) if vs.irules else ""
            vs_table.add_row(name, vs.destination, vs.pool, irules)

        self.console.print(vs_table)

        # Pools
        pool_table = Table(title="Pools", box=box.ROUNDED)
        pool_table.add_column("Name", style="bold yellow")
        pool_table.add_column("Monitor")
        pool_table.add_column("LB Method")
        pool_table.add_column("Members", style="green")

        for name, pool in config.pools.items():
            members = ", ".join(str(m) for m in pool.members)
            pool_table.add_row(name, pool.monitor, pool.lb_method, members)

        self.console.print()
        self.console.print(pool_table)

    # ── Top Message Codes ───────────────────────────────────────

    def print_top_msg_codes(self, codes: list[tuple[str, int, str]]):
        """Print top F5 message codes."""
        if not codes:
            return

        self.console.print()
        table = Table(title="Top F5 Message Codes", box=box.ROUNDED)
        table.add_column("Code", style="bold cyan")
        table.add_column("Count", justify="right")
        table.add_column("Sample Message")

        for code, count, sample in codes:
            msg = sample[:80] + "..." if len(sample) > 80 else sample
            table.add_row(code, f"{count:,}", msg)

        self.console.print(table)

    # ── JSON Export ─────────────────────────────────────────────

    @staticmethod
    def to_json(
        meta: DeviceMeta,
        entries: list[dict],
        findings: list[Finding],
        config: Optional[BigIPConfig] = None,
        qkview_data: Optional[QKViewData] = None,
    ) -> str:
        """Export analysis results as JSON.

        When qkview_data is supplied, F5OS-specific extras (quick-link command
        outputs, health entries, system-events text) are included so the webapp
        can render them without re-opening the archive.
        """
        data = {
            "device_info": {
                "product": meta.product,
                "version": meta.version,
                "build": meta.build,
                "edition": meta.edition,
                "platform": meta.platform,
                "hostname": meta.hostname,
                "cores": meta.cores,
                "memory_mb": meta.memory_mb,
                "base_mac": meta.base_mac,
                "generation_date": meta.generation_date,
            },
            "findings": [f.to_dict() for f in findings],
            "entries": entries,
            "entry_count": len(entries),
        }

        if config:
            data["config"] = {
                "virtual_servers": {
                    name: {
                        "destination": vs.destination,
                        "pool": vs.pool,
                        "irules": vs.irules,
                    }
                    for name, vs in config.virtual_servers.items()
                },
                "pools": {
                    name: {
                        "monitor": pool.monitor,
                        "lb_method": pool.lb_method,
                        "members": [str(m) for m in pool.members],
                    }
                    for name, pool in config.pools.items()
                },
            }

        if qkview_data is not None:
            if qkview_data.f5os_commands:
                data["f5os_commands"] = qkview_data.f5os_commands
            if qkview_data.f5os_overview is not None:
                ov = qkview_data.f5os_overview
                data["f5os_overview"] = {
                    "generation_start": ov.generation_start,
                    "generation_stop": ov.generation_stop,
                    "platform_pid": ov.platform_pid,
                    "platform_code": ov.platform_code,
                    "platform_part_number": ov.platform_part_number,
                    "platform_uuid": ov.platform_uuid,
                    "platform_slot": ov.platform_slot,
                    "version_edition": ov.version_edition,
                    "cluster_summary": ov.cluster_summary,
                    "cluster_nodes": [
                        {
                            "name": n.name,
                            "running_state": n.running_state,
                            "ready": n.ready,
                            "ready_message": n.ready_message,
                            "slot": n.slot,
                        }
                        for n in ov.cluster_nodes
                    ],
                    "mgmt_ipv4_address": ov.mgmt_ipv4_address,
                    "mgmt_ipv4_prefix": ov.mgmt_ipv4_prefix,
                    "mgmt_ipv4_gateway": ov.mgmt_ipv4_gateway,
                    "mgmt_ipv6_address": ov.mgmt_ipv6_address,
                    "mgmt_ipv6_prefix": ov.mgmt_ipv6_prefix,
                    "mgmt_ipv6_gateway": ov.mgmt_ipv6_gateway,
                    "payg_license_level": ov.payg_license_level,
                    "licensed_version": ov.licensed_version,
                    "registration_key": ov.registration_key,
                    "licensed_date": ov.licensed_date,
                    "serial_number": ov.serial_number,
                    "time_zone": ov.time_zone,
                    "appliance_datetime": ov.appliance_datetime,
                    "appliance_mode": ov.appliance_mode,
                    "portgroups": [
                        {"id": p.id, "mode": p.mode} for p in ov.portgroups
                    ],
                    "tenants": [
                        {
                            "name": t.name,
                            "type": t.type,
                            "running_state": t.running_state,
                            "status": t.status,
                            "image_version": t.image_version,
                            "mgmt_ip": t.mgmt_ip,
                            "vcpu_cores_per_node": t.vcpu_cores_per_node,
                            "memory_mb": t.memory_mb,
                        }
                        for t in ov.tenants
                    ],
                    "tenants_configured": ov.tenants_configured,
                    "tenants_provisioned": ov.tenants_provisioned,
                    "tenants_deployed": ov.tenants_deployed,
                    "tenants_running": ov.tenants_running,
                }
            if qkview_data.f5os_health:
                data["f5os_health"] = [
                    {
                        "component": h.component,
                        "health": h.health,
                        "severity": h.severity,
                        "attribute": h.attribute,
                        "description": h.description,
                        "value": h.value,
                        "updated_at": h.updated_at,
                    }
                    for h in qkview_data.f5os_health
                ]
            if qkview_data.diag_files:
                data["diag_files"] = sorted(qkview_data.diag_files.keys())
            if qkview_data.xml_stats is not None:
                xs = qkview_data.xml_stats
                # Full lists for VS/pools/members get large fast (160+ fields
                # each). Summary counts plus top-N sorted views are enough
                # for the current UI, and keep the response well under 10 MB.
                data["xml_stats"] = {
                    "summary": xs.summary(),
                    "top_virtual_servers": [
                        r.fields for r in xs.top_virtual_servers(20)
                    ],
                    "top_pools": [r.fields for r in xs.top_pools(20)],
                    # Top-N pool members by serverside.tot_conns. Full list
                    # can reach thousands on busy TMOS LTM deployments; 30
                    # is enough to surface the traffic hot-spots the webapp
                    # renders in its members panel.
                    "top_pool_members": [
                        r.fields for r in xs.top_pool_members(30)
                    ],
                    # TMMs / interfaces / CPUs ship deduped — TMOS emits
                    # multiple replica rows per resource (one per sample
                    # window, one per plane, etc.) and the webapp panels
                    # render these lists directly, so dedupe upstream
                    # rather than forcing the frontend to collapse them.
                    "tmms": [r.fields for r in xs.deduped_tmms()],
                    "interfaces": [r.fields for r in xs.deduped_interfaces()],
                    "cpus": [r.fields for r in xs.deduped_cpus()],
                    "active_modules": [r.fields for r in xs.active_modules],
                    "asm_policies": [r.fields for r in xs.asm_policies],
                    # Certs typically number in the hundreds (941 on the
                    # reference archive). Ship only the 50 soonest-expiring
                    # so the UI can render an expiry panel without inflating
                    # the NDJSON payload.
                    "top_expiring_certificates": [
                        r.fields for r in xs.top_expiring_certificates(50)
                    ],
                }

        return json.dumps(data, indent=2, default=str)
