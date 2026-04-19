"""Parse F5 BIG-IP syslog-format log entries."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# F5 syslog format:
# MMM DD HH:MM:SS hostname severity process[PID]: MSGCODE:LEVEL: message
# or without F5 message code:
# MMM DD HH:MM:SS hostname severity process[PID]: message

_SYSLOG_RE = re.compile(
    r"^(?P<month>\w{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<severity>\w+)\s+"
    r"(?P<process>[^\[:\s]+)"
    r"(?:\[(?P<pid>\d+)\])?"
    r":\s*(?:(?P<msg_code>[0-9a-fA-F]{8}):(?P<f5_sev>\d):)?\s*"
    r"(?P<message>.*)"
)

# ISO 8601 format (F5OS / Velos / rSeries):
# 2024-03-26T19:37:03.123+00:00 hostname severity process[PID]: message
_ISO8601_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<severity>\w+)\s+"
    r"(?P<process>[^\[:\s]+)"
    r"(?:\[(?P<pid>\d+)\])?"
    r":\s*"
    r"(?P<message>.*)"
)

# F5OS structured platform log format:
# May 08 13:08:07 lopd lopd[22]: priority="Notice" version=1.0 msgid=0x6201000000000022 msg="LOP daemon LOPD starting"
_F5OS_STRUCTURED_RE = re.compile(
    r"^(?P<month>\w{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<process>[^\[:\s]+)"
    r"(?:\[(?P<pid>\d+)\])?"
    r":\s*priority=\"(?P<priority>[^\"]+)\""
    r"(?:\s+version=\S+)?"
    r"(?:\s+msgid=(?P<msgid>\S+))?"
    r"(?:\s+msg=\"(?P<msg>[^\"]*)\")?"
    r"(?P<extra>.*)"
)

# F5OS structured with ISO 8601 timestamp (VELOS partition/syscon velos.log):
# 2026-01-16T00:57:13.773098+00:00 mseg lopd[22]: nodename=blade-1(p1) priority="Info" msgid=0x... msg="..."
# No standalone severity field; severity rides inside as priority="...".
_ISO8601_F5OS_STRUCTURED_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\s+"
    r"(?P<hostname>\S+)\s+"
    r"(?P<process>[^\[:\s]+)"
    r"(?:\[(?P<pid>\d+)\])?"
    r":\s*"
    r"(?:nodename=\S+\s+)?"
    r"priority=\"(?P<priority>[^\"]+)\""
    r"(?:\s+version=\S+)?"
    r"(?:\s+msgid=(?P<msgid>\S+))?"
    r"(?:\s+msg=\"(?P<msg>[^\"]*)\")?"
    r"(?P<extra>.*)"
)

# F5OS event log format (event-log.log and "show system events" output):
# 65550 appliance firmware-update-status EVENT NA "Firmware update..." "2024-01-15 14:20:48.019561540 UTC"
_F5OS_EVENT_RE = re.compile(
    r"^(?P<event_id>\d+)\s+"
    r"(?P<source>\S+)\s+"
    r"(?P<event_type>\S+)\s+"
    r"(?P<category>\S+)\s+"
    r"(?P<severity_cat>\S+)\s+"
    r'"(?P<message>[^"]*)"\s+'
    r'"(?P<timestamp>[^"]*)"'
)

# Map F5OS priority names to standard syslog severity levels
_F5OS_PRIORITY_MAP = {
    "emergency": "emerg",
    "alert": "alert",
    "critical": "crit",
    "error": "err",
    "err": "err",
    "warn": "warning",
    "warning": "warning",
    "notice": "notice",
    "info": "info",
    "debug": "debug",
}

# Severity levels (syslog standard)
SEVERITY_LEVELS = {
    "emerg": 0,
    "alert": 1,
    "crit": 2,
    "err": 3,
    "warning": 4,
    "notice": 5,
    "info": 6,
    "debug": 7,
}

# Reverse lookup
SEVERITY_NAMES = {v: k for k, v in SEVERITY_LEVELS.items()}


@dataclass
class LogEntry:
    """A single parsed log entry."""
    timestamp: datetime
    hostname: str
    severity: str
    severity_num: int
    process: str
    pid: Optional[int]
    msg_code: Optional[str]
    f5_severity: Optional[int]
    message: str
    source_file: str
    line_number: int
    raw_line: str

    @property
    def effective_severity(self) -> int:
        """Return the most specific severity (F5 code > syslog)."""
        if self.f5_severity is not None:
            return self.f5_severity
        return self.severity_num


def _infer_year(month: str, day: int) -> int:
    """Infer the year for a log entry.

    F5 syslog entries don't include the year. We use the current year,
    and if the date is in the future, assume previous year.
    """
    now = datetime.now()
    month_num = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
        "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
        "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }.get(month, 1)

    year = now.year
    try:
        candidate = datetime(year, month_num, day)
        if candidate > now:
            year -= 1
    except ValueError:
        pass

    return year


def parse_line(line: str, source_file: str = "", line_number: int = 0) -> Optional[LogEntry]:
    """Parse a single syslog, ISO 8601, or F5OS structured line into a LogEntry.

    Returns None if the line doesn't match any expected format.
    """
    line = line.rstrip("\n\r")
    if not line:
        return None

    # Try ISO 8601 F5OS structured first (VELOS partition/syscon velos.log).
    # Before this match existed, each of these lines fell through to the
    # continuation branch and glued together into 100 MB "single entries".
    match = _ISO8601_F5OS_STRUCTURED_RE.match(line)
    if match:
        g = match.groupdict()
        try:
            ts_str = g["timestamp"]
            if ts_str.endswith("Z"):
                ts_str = ts_str.replace("Z", "+00:00")
            timestamp = datetime.fromisoformat(ts_str)
            if timestamp.tzinfo is not None:
                timestamp = timestamp.astimezone(tz=None).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None

        priority = (g.get("priority") or "").lower()
        severity = _F5OS_PRIORITY_MAP.get(priority, "info")
        severity_num = SEVERITY_LEVELS.get(severity, 6)
        pid = int(g["pid"]) if g["pid"] else None
        message = g.get("msg") or ""
        extra = (g.get("extra") or "").strip()
        if extra:
            message = f"{message} {extra}" if message else extra

        return LogEntry(
            timestamp=timestamp,
            hostname=g["hostname"],
            severity=severity,
            severity_num=severity_num,
            process=g["process"],
            pid=pid,
            msg_code=g.get("msgid"),
            f5_severity=None,
            message=message,
            source_file=source_file,
            line_number=line_number,
            raw_line=line,
        )

    # Try F5OS structured (short-date) format
    match = _F5OS_STRUCTURED_RE.match(line)
    if match:
        g = match.groupdict()
        month = g["month"]
        day = int(g["day"])
        time_str = g["time"]
        year = _infer_year(month, day)
        month_num = {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
            "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
            "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
        }.get(month, 1)
        try:
            h, m, s = time_str.split(":")
            timestamp = datetime(year, month_num, day, int(h), int(m), int(s))
        except (ValueError, TypeError):
            return None

        # Map F5OS priority to standard severity
        priority = g["priority"].lower()
        severity = _F5OS_PRIORITY_MAP.get(priority, "info")
        severity_num = SEVERITY_LEVELS.get(severity, 6)
        pid = int(g["pid"]) if g["pid"] else None
        msg_code = g.get("msgid")
        # Build message from msg field + any extra key-value pairs
        message = g.get("msg", "") or ""
        extra = g.get("extra", "").strip()
        if extra:
            message = f"{message} {extra}" if message else extra

        return LogEntry(
            timestamp=timestamp,
            hostname=g["hostname"],
            severity=severity,
            severity_num=severity_num,
            process=g["process"],
            pid=pid,
            msg_code=msg_code,
            f5_severity=None,
            message=message,
            source_file=source_file,
            line_number=line_number,
            raw_line=line,
        )

    # Try TMOS Syslog format
    match = _SYSLOG_RE.match(line)
    if match:
        g = match.groupdict()
        month = g["month"]
        day = int(g["day"])
        time_str = g["time"]
        year = _infer_year(month, day)
        month_num = {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
            "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
            "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
        }.get(month, 1)
        try:
            h, m, s = time_str.split(":")
            timestamp = datetime(year, month_num, day, int(h), int(m), int(s))
        except (ValueError, TypeError):
            return None
        
        msg_code = g["msg_code"]
        f5_severity = int(g["f5_sev"]) if g["f5_sev"] else None
    else:
        # Try F5OS ISO 8601 format
        match = _ISO8601_RE.match(line)
        if not match:
            return None
        
        g = match.groupdict()
        try:
            # handle 'Z' or offset
            ts_str = g["timestamp"]
            if ts_str.endswith('Z'):
                ts_str = ts_str.replace('Z', '+00:00')
            timestamp = datetime.fromisoformat(ts_str)
            # Normalize to naive UTC so mixed log sources sort together.
            # F5OS event logs are parsed as naive, so mixing tz-aware ISO 8601
            # entries with those would raise TypeError in the downstream sort.
            if timestamp.tzinfo is not None:
                timestamp = timestamp.astimezone(tz=None).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None

        msg_code = None
        f5_severity = None

    # Common fields for TMOS syslog and ISO 8601 formats
    severity = g["severity"].lower()
    severity_num = SEVERITY_LEVELS.get(severity, 6)  # default to info
    pid = int(g["pid"]) if g["pid"] else None
    message = g["message"].strip()

    return LogEntry(
        timestamp=timestamp,
        hostname=g["hostname"],
        severity=severity,
        severity_num=severity_num,
        process=g["process"],
        pid=pid,
        msg_code=msg_code,
        f5_severity=f5_severity,
        message=message,
        source_file=source_file,
        line_number=line_number,
        raw_line=line,
    )


# Continuation lines in RFC-style syslog start with whitespace (tab or space).
# Anything else after a parsed entry is almost always an unrecognised-format
# line or a raw diagnostic dump — gluing them in produced 100 MB "single
# entries" on VELOS partition velos.log. Cap the accumulator as a backstop.
_MAX_CONTINUATION_BYTES = 32 * 1024


def parse_log_content(
    content: str,
    source_file: str = "",
    progress_callback=None,
) -> list[LogEntry]:
    """Parse all entries from a log file's content.

    Multi-line continuation: only whitespace-indented lines following a parsed
    entry are treated as continuation. Accumulator is bounded; anything past
    the cap is replaced with a truncation marker.
    """
    entries = []
    lines = content.splitlines()
    total_lines = len(lines)
    current_entry: Optional[LogEntry] = None
    msg_chunks: list[str] = []
    raw_chunks: list[str] = []
    cont_bytes = 0
    cont_capped = False

    def _flush():
        if current_entry is None:
            return
        if msg_chunks:
            current_entry.message = "\n".join([current_entry.message, *msg_chunks])
            current_entry.raw_line = "\n".join([current_entry.raw_line, *raw_chunks])
        if cont_capped:
            marker = f"\n…[continuation truncated at {_MAX_CONTINUATION_BYTES} bytes]"
            current_entry.message += marker
            current_entry.raw_line += marker
        entries.append(current_entry)

    for i, line in enumerate(lines):
        if progress_callback and i % 5000 == 0:
            progress_callback(f"Parsing {source_file}: line {i}/{total_lines}")

        parsed = parse_line(line, source_file, i + 1)

        if parsed is not None:
            _flush()
            current_entry = parsed
            msg_chunks = []
            raw_chunks = []
            cont_bytes = 0
            cont_capped = False
        elif (
            current_entry is not None
            and line
            and line[0] in (" ", "\t")
            and line.strip()
        ):
            if cont_bytes < _MAX_CONTINUATION_BYTES:
                stripped = line.strip()
                msg_chunks.append(stripped)
                raw_chunks.append(line)
                cont_bytes += len(line) + 1
            else:
                cont_capped = True
        # Non-whitespace-prefixed unparseable lines are dropped.

    _flush()
    return entries


def parse_f5os_event_log(
    content: str,
    source_file: str = "event-log",
) -> list[LogEntry]:
    """Parse F5OS event log format.

    Format: <event_id> <source> <event_type> <category> <severity> "<message>" "<timestamp UTC>"
    """
    entries = []
    for i, line in enumerate(content.splitlines()):
        line = line.strip()
        if not line:
            continue

        # Skip table headers (lines starting with LOG, dashes, or non-digit)
        if not line[0].isdigit():
            continue

        match = _F5OS_EVENT_RE.match(line)
        if not match:
            continue

        g = match.groupdict()
        try:
            # Parse timestamp like "2024-01-15 14:20:48.019561540 UTC"
            ts_str = g["timestamp"].replace(" UTC", "").strip()
            # Truncate nanoseconds to microseconds for datetime
            if "." in ts_str:
                date_part, frac = ts_str.split(".")
                frac = frac[:6]  # max 6 digits for microseconds
                ts_str = f"{date_part}.{frac}"
            timestamp = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue

        # Map event severity category
        sev_cat = g.get("severity_cat", "NA").lower()
        event_type = g.get("event_type", "").lower()
        if sev_cat in ("critical", "crit"):
            severity = "crit"
        elif sev_cat in ("error", "err"):
            severity = "err"
        elif sev_cat in ("warning", "warn"):
            severity = "warning"
        elif event_type == "assert":
            severity = "warning"
        else:
            severity = "notice"

        severity_num = SEVERITY_LEVELS.get(severity, 5)
        source = g.get("source", "")
        event_type_str = g.get("event_type", "")
        message = g.get("message", "")

        entries.append(LogEntry(
            timestamp=timestamp,
            hostname=source,
            severity=severity,
            severity_num=severity_num,
            process=event_type_str,
            pid=None,
            msg_code=g.get("event_id"),
            f5_severity=None,
            message=message,
            source_file=source_file,
            line_number=i + 1,
            raw_line=line,
        ))

    return entries


def parse_all_logs(
    log_files: dict[str, str],
    progress_callback=None,
) -> list[LogEntry]:
    """Parse all log files into a flat list of LogEntries, sorted by timestamp.

    Args:
        log_files: dict of {log_name: content}
        progress_callback: Optional callable(status_message)

    Returns:
        List of LogEntry objects sorted by timestamp
    """
    all_entries = []
    total_files = len(log_files)

    for idx, (name, content) in enumerate(log_files.items()):
        if progress_callback:
            progress_callback(f"[{idx + 1}/{total_files}] Parsing {name}...")

        entries = parse_log_content(content, source_file=name)
        all_entries.extend(entries)

    # Sort by timestamp
    all_entries.sort(key=lambda e: e.timestamp)

    if progress_callback:
        progress_callback(f"Parsed {len(all_entries)} log entries from {total_files} files")

    return all_entries
