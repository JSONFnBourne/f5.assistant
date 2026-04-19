"""Tests for parser.parse_log_content edge cases.

Guardrails against the two session-11 regressions:
  1. VELOS partition velos.log lines (ISO 8601 + F5OS structured with
     embedded priority) must parse as individual entries, not collapse
     into a single 100 MB continuation chain.
  2. Multi-line continuation must require whitespace-indented follow-on
     lines AND be bounded, so no future unknown format can reproduce the
     over-aggregation bug.
"""

from qkview_analyzer.parser import (
    _MAX_CONTINUATION_BYTES,
    parse_line,
    parse_log_content,
)


def test_iso8601_f5os_structured_line_parses():
    line = (
        '2026-01-16T00:57:13.773098+00:00 mseg lopd[22]: '
        'nodename=blade-1(p1) priority="Info" version=1.0 msgid=0x6201000000000022 '
        'msg="LOP daemon LOPD starting"'
    )
    e = parse_line(line)
    assert e is not None
    assert e.process == "lopd"
    assert e.pid == 22
    assert e.hostname == "mseg"
    assert e.severity == "info"
    assert e.msg_code == "0x6201000000000022"
    assert e.message == "LOP daemon LOPD starting"


def test_velos_partition_velos_log_no_over_aggregation():
    # The real VELOS partition velos.log shape: a boot_marker header followed
    # by many ISO 8601 F5OS-structured lines. Before the fix, only the first
    # line parsed and all others became continuation, producing one entry
    # with a 100 MB raw_line. Assert we now get one entry per line.
    header = (
        "2026-01-16T00:55:04+00:00 blade-1.chassis.local notice boot_marker: "
        "---===[ BOOT-MARKER F5OS-C blade version 1.8.2-28311 ]===---"
    )
    structured_lines = [
        f'2026-01-16T00:57:{i:02d}.000000+00:00 mseg lopd[22]: '
        f'nodename=blade-1(p1) priority="Info" msgid=0x6201000000000022 '
        f'msg="tick {i}"'
        for i in range(30)
    ]
    content = "\n".join([header, *structured_lines])
    entries = parse_log_content(content, source_file="velos.log")
    assert len(entries) == 1 + len(structured_lines)
    assert entries[0].process == "boot_marker"
    assert entries[1].process == "lopd"
    # No entry should have absorbed the others
    max_raw = max(len(e.raw_line) for e in entries)
    assert max_raw < 1024


def test_indented_continuation_is_appended():
    # Python traceback-style: the exception header parses, and the indented
    # traceback lines that follow are legitimately its continuation.
    content = (
        "Jan 15 10:00:00 host-1 err restjavad[123]: Exception raised\n"
        "\tat com.f5.rest.handler.Foo\n"
        "\tat com.f5.rest.handler.Bar\n"
        "Jan 15 10:00:01 host-1 info restjavad[123]: next line"
    )
    entries = parse_log_content(content)
    assert len(entries) == 2
    assert "com.f5.rest.handler.Foo" in entries[0].message
    assert "com.f5.rest.handler.Bar" in entries[0].message
    assert entries[1].message == "next line"


def test_unindented_junk_after_entry_is_dropped():
    # The failure mode the session-11 fix targets: a parseable header
    # followed by unindented raw dump lines. Old code glued them in.
    content = (
        "Jan 15 10:00:00 host-1 err app[1]: boom\n"
        "RAW_DUMP_LINE_1_not_indented\n"
        "RAW_DUMP_LINE_2_not_indented\n"
        "Jan 15 10:00:01 host-1 info app[1]: next"
    )
    entries = parse_log_content(content)
    assert len(entries) == 2
    assert entries[0].message == "boom"
    assert "RAW_DUMP" not in entries[0].raw_line
    assert entries[1].message == "next"


def test_continuation_is_bounded():
    # If a pathological log does produce legitimate indented continuations
    # larger than the cap, we stop appending and emit a truncation marker.
    huge_line = "\t" + ("x" * 2048)
    lines = ["Jan 15 10:00:00 host-1 err app[1]: header"]
    lines.extend([huge_line] * 50)  # ~100 KB of continuation
    lines.append("Jan 15 10:00:01 host-1 info app[1]: next")
    entries = parse_log_content("\n".join(lines))
    assert len(entries) == 2
    assert "continuation truncated" in entries[0].raw_line
    assert len(entries[0].raw_line) < _MAX_CONTINUATION_BYTES + 4096
