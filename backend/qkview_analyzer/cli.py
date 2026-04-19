"""CLI entry point for qkview-analyzer."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .extractor import extract_qkview
from .parser import parse_all_logs, parse_f5os_event_log
from .indexer import LogIndexer
from .config_parser import parse_bigip_conf, parse_bigip_base_conf, BigIPConfig
from .rule_engine import RuleEngine
from .reporter import Reporter
from .tmstat_parser import parse_tmstat_files

console = Console()


def _load_and_index(qkview_file: str) -> tuple:
    """Extract, parse, and index a qkview file.

    Returns (data, indexer, config, entries) tuple.
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        # Extract
        task = progress.add_task("Extracting qkview archive...", total=None)
        data = extract_qkview(
            qkview_file,
            progress_callback=lambda msg: progress.update(task, description=msg),
        )
        progress.update(task, description=f"Extracted {len(data.log_files)} log files")

        # Parse logs
        progress.update(task, description="Parsing log entries...")
        entries = parse_all_logs(
            data.log_files,
            progress_callback=lambda msg: progress.update(task, description=msg),
        )

        # F5OS: also parse event log and system events
        if data.meta.product == "F5OS":
            if data.f5os_event_log:
                entries.extend(parse_f5os_event_log(data.f5os_event_log, source_file="event-log.log"))
            if data.f5os_system_events:
                entries.extend(parse_f5os_event_log(data.f5os_system_events, source_file="system-events"))
            entries.sort(key=lambda e: e.timestamp)

        # Index
        progress.update(task, description="Building search index...")
        indexer = LogIndexer()
        indexer.bulk_insert(
            entries,
            progress_callback=lambda msg: progress.update(task, description=msg),
        )

        # Parse config
        progress.update(task, description="Parsing configuration...")
        config = BigIPConfig()
        if "config/bigip.conf" in data.config_files:
            config = parse_bigip_conf(data.config_files["config/bigip.conf"])

        if "config/bigip_base.conf" in data.config_files:
            base_config = parse_bigip_base_conf(
                data.config_files["config/bigip_base.conf"]
            )
            config.vlans = base_config.vlans
            config.self_ips = base_config.self_ips

        progress.update(task, description="[green]✓ Ready[/]")

    return data, indexer, config, entries


@click.group()
@click.version_option(version="0.1.0", prog_name="qkview-analyzer")
def cli():
    """F5 BIG-IP QKView Log Analyzer.

    Automated log analysis, known-issue detection, and configuration context
    for F5 BIG-IP qkview diagnostic archives.
    """
    pass


@cli.command()
@click.argument("qkview_file", type=click.Path(exists=True))
@click.option("--start", "-s", help="Start time filter (YYYY-MM-DD HH:MM)")
@click.option("--end", "-e", help="End time filter (YYYY-MM-DD HH:MM)")
@click.option(
    "--severity", "-v",
    type=click.Choice(
        ["debug", "info", "notice", "warning", "err", "crit", "alert", "emerg"],
        case_sensitive=False,
    ),
    help="Minimum severity level",
)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def analyze(qkview_file: str, start: str, end: str, severity: str, json_output: bool):
    """Full analysis with known-issue detection.

    Extracts logs, parses configuration, runs known-issue rules,
    and presents a comprehensive summary.
    """
    data, indexer, config, entries = _load_and_index(qkview_file)
    reporter = Reporter(console)

    # Parse time filters
    start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M") if start else None
    end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M") if end else None

    # Run known-issue scan
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning for known issues...", total=None)
        platform = "f5os" if data.meta.product == "F5OS" else "tmos"
        engine = RuleEngine(platform=platform)
        findings = engine.scan(
            indexer,
            progress_callback=lambda msg: progress.update(task, description=msg),
        )
        progress.update(task, description=f"[green]✓ Scan complete — {len(findings)} issue(s) found[/]")

    if json_output:
        # JSON export
        queried = indexer.query(start=start_dt, end=end_dt, min_severity=severity, limit=50000)
        json_str = Reporter.to_json(data.meta, queried, findings, config)
        click.echo(json_str)
        return

    # Terminal output
    console.print()
    console.rule("[bold blue]QKView Analysis Report[/]")

    # Device summary
    reporter.print_device_summary(data.meta)

    # Log statistics
    time_range = indexer.get_time_range()
    severity_summary = indexer.get_severity_summary()
    process_summary = indexer.get_process_summary()
    source_summary = indexer.get_source_summary()

    reporter.print_log_stats(
        total_entries=indexer.entry_count,
        time_range=time_range,
        severity_summary=severity_summary,
        source_summary=source_summary,
        process_summary=process_summary,
    )

    # Top message codes
    top_codes = indexer.get_top_msg_codes(15)
    reporter.print_top_msg_codes(top_codes)

    # Known issues
    reporter.print_findings(findings)

    # Config summary
    if config.virtual_servers or config.pools:
        console.print()
        console.rule("[bold blue]Configuration Context[/]")
        reporter.print_config_summary(config)

    # tmstat summary
    if data.tmstat_files:
        tmstat = parse_tmstat_files(data.tmstat_files)
        console.print()
        console.print(
            f"[dim]tmstat snapshots: {tmstat.snapshot_count} files, "
            f"categories: {', '.join(tmstat.categories)}, "
            f"intervals: {', '.join(tmstat.time_ranges)}[/]"
        )

    console.print()
    console.rule("[dim]Analysis complete[/]")
    indexer.close()


@cli.command()
@click.argument("qkview_file", type=click.Path(exists=True))
@click.option("--start", "-s", help="Start time filter (YYYY-MM-DD HH:MM)")
@click.option("--end", "-e", help="End time filter (YYYY-MM-DD HH:MM)")
@click.option(
    "--severity", "-v",
    type=click.Choice(
        ["debug", "info", "notice", "warning", "err", "crit", "alert", "emerg"],
        case_sensitive=False,
    ),
    help="Minimum severity level",
)
@click.option("--process", "-p", help="Filter by process name")
@click.option("--search", "-q", help="Full-text search term")
@click.option("--code", "-c", help="F5 message code filter")
@click.option("--source", help="Source log file filter (e.g. 'ltm')")
@click.option("--limit", "-n", default=100, help="Max entries to show (default: 100)")
@click.option("--table", "table_mode", is_flag=True, help="Show in table format")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def logs(
    qkview_file: str,
    start: str,
    end: str,
    severity: str,
    process: str,
    search: str,
    code: str,
    source: str,
    limit: int,
    table_mode: bool,
    json_output: bool,
):
    """Browse and filter log entries.

    View log entries with flexible filtering by time, severity,
    process, message code, or full-text search.
    """
    data, indexer, config, entries = _load_and_index(qkview_file)
    reporter = Reporter(console)

    # Parse time filters
    start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M") if start else None
    end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M") if end else None

    # Query
    results = indexer.query(
        start=start_dt,
        end=end_dt,
        min_severity=severity,
        process=process,
        msg_code=code,
        search=search,
        source_file=source,
        limit=limit,
    )

    if json_output:
        import json
        click.echo(json.dumps(results, indent=2, default=str))
    elif table_mode:
        reporter.print_log_entries_table(results)
    else:
        reporter.print_log_entries(results)

    console.print(f"\n[dim]Showing {len(results)} of {indexer.entry_count} total entries[/]")
    indexer.close()


@cli.command()
@click.argument("qkview_file", type=click.Path(exists=True))
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def config(qkview_file: str, json_output: bool):
    """Show parsed configuration context.

    Displays virtual servers, pools, members, and their relationships.
    """
    data, indexer, config_obj, entries = _load_and_index(qkview_file)
    reporter = Reporter(console)

    if json_output:
        import json
        output = {
            "virtual_servers": {
                name: {
                    "destination": vs.destination,
                    "pool": vs.pool,
                    "irules": vs.irules,
                    "profiles": vs.profiles,
                }
                for name, vs in config_obj.virtual_servers.items()
            },
            "pools": {
                name: {
                    "monitor": pool.monitor,
                    "lb_method": pool.lb_method,
                    "members": [str(m) for m in pool.members],
                }
                for name, pool in config_obj.pools.items()
            },
        }
        click.echo(json.dumps(output, indent=2))
    else:
        console.rule("[bold blue]BIG-IP Configuration[/]")
        reporter.print_config_summary(config_obj)

    indexer.close()


@cli.command()
@click.argument("qkview_file", type=click.Path(exists=True))
@click.argument("output_file", type=click.Path())
@click.option("--start", "-s", help="Start time filter (YYYY-MM-DD HH:MM)")
@click.option("--end", "-e", help="End time filter (YYYY-MM-DD HH:MM)")
@click.option("--severity", "-v", help="Minimum severity level")
def export(qkview_file: str, output_file: str, start: str, end: str, severity: str):
    """Export full analysis results to JSON file.

    Exports device info, all findings, filtered log entries,
    and configuration context to a JSON file.
    """
    data, indexer, config_obj, entries = _load_and_index(qkview_file)

    # Parse time filters
    start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M") if start else None
    end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M") if end else None

    # Run rules
    platform = "f5os" if data.meta.product == "F5OS" else "tmos"
    engine = RuleEngine(platform=platform)
    findings = engine.scan(indexer)

    # Query entries
    queried = indexer.query(start=start_dt, end=end_dt, min_severity=severity, limit=100000)

    # Export
    json_str = Reporter.to_json(data.meta, queried, findings, config_obj)

    with open(output_file, "w") as f:
        f.write(json_str)

    console.print(f"[green]✓ Exported {len(queried)} entries and {len(findings)} findings to {output_file}[/]")
    indexer.close()


@cli.command()
@click.argument("qkview_file", type=click.Path(exists=True))
def info(qkview_file: str):
    """Show device info and archive summary.

    Quick look at device metadata without full log analysis.
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Reading qkview metadata...", total=None)
        data = extract_qkview(
            qkview_file,
            progress_callback=lambda msg: progress.update(task, description=msg),
        )

    reporter = Reporter(console)
    reporter.print_device_summary(data.meta)

    console.print(f"\n[dim]Archive contents:[/]")
    console.print(f"  Log files:    {len(data.log_files)}")
    console.print(f"  Config files: {len(data.config_files)}")
    console.print(f"  tmstat files: {len(data.tmstat_files)}")


if __name__ == "__main__":
    cli()
