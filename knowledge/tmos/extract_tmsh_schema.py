"""Extract tmsh command documentation from the TMOS 17.0 pdf into JSON.

This is a best-effort scraper that walks the pdf line-by-line, finds each
`NAME` section, and captures the surrounding sections (MODULE, SYNTAX,
DESCRIPTION, EXAMPLES, OPTIONS, etc.). The output is intentionally raw so
that downstream tooling can normalize or enrich it as needed.

Usage:
    python extract_tmsh_schema.py --pdf tmsh_17.0.0.pdf --out tmsh_commands_raw.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

from pypdf import PdfReader


# Upper-case markers that delimit sections inside each command stanza.
HEADING_TOKENS = {
    "MODULE",
    "MODULES",
    "SYNTAX",
    "DESCRIPTION",
    "EXAMPLES",
    "EXAMPLE",
    "OPTIONS",
    "DISPLAY",
    "SEE ALSO",
    "COPYRIGHT",
    "NOTES",
    "OVERVIEW",
    "DIAGNOSTICS",
    "INPUT",
    "OUTPUT",
    "AUTHORIZATION",
    "FORM",
    "FILES",
    "DELETE",
    "CREATE/MODIFY",
    "CREATE",
    "MODIFY",
    "EDIT",
}


def parse_tmsh_pdf(pdf_path: Path) -> List[Dict[str, str]]:
    """Parse the tmsh PDF into a list of command dictionaries."""
    reader = PdfReader(str(pdf_path))

    commands: List[Dict[str, object]] = []
    current: Dict[str, object] | None = None
    section: str | None = None
    prev_nonempty: str = ""

    for page in reader.pages:
        text = page.extract_text()
        if not text:
            continue

        for raw in text.splitlines():
            stripped = raw.strip()

            # A NAME marker signals a new command. The command id is the last
            # non-empty line we saw (typically the command path such as
            # "apm aaa crldp").
            if stripped == "NAME":
                if current and section:
                    tail = current["sections"].get(section, [])
                    if tail:
                        candidate = tail[-1].strip()
                        # If the last line we appended looks like the next
                        # command id (single token, unindented), drop it so
                        # we do not pollute the previous section.
                        if (
                            candidate == prev_nonempty
                            and " " not in candidate
                            and len(candidate) < 40
                        ):
                            tail.pop()

                if current:
                    commands.append(current)

                current = {"id": prev_nonempty, "sections": {"NAME": []}, "module_path": []}
                section = "NAME"
                continue

            if current and stripped in HEADING_TOKENS:
                section = stripped
                current["sections"].setdefault(section, [])
                continue

            if stripped:
                prev_nonempty = stripped

            if current and section:
                current["sections"][section].append(raw)

    if current:
        commands.append(current)

    # Post-process: normalize sections, derive module path, and add a summary.
    normalized: List[Dict[str, str]] = []
    for cmd in commands:
        sections = cmd["sections"]
        for key, value in list(sections.items()):
            sections[key] = "\n".join(value).strip()

        parts = cmd["id"].split()
        if len(parts) > 1:
            module_path, command = parts[:-1], parts[-1]
        else:
            module_path, command = [], cmd["id"]

        normalized.append(
            {
                "id": cmd["id"],
                "command": command,
                "module_path": module_path,
                "summary": sections.get("NAME", ""),
                "sections": sections,
            }
        )

    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract tmsh command docs into JSON.")
    parser.add_argument("--pdf", type=Path, default=Path("tmsh_17.0.0.pdf"), help="Path to tmsh_17.0.0.pdf")
    parser.add_argument("--out", type=Path, default=Path("tmsh_commands_raw.json"), help="Where to write the JSON output")
    args = parser.parse_args()

    commands = parse_tmsh_pdf(args.pdf)
    args.out.write_text(json.dumps(commands, indent=2))
    print(f"Wrote {len(commands)} commands to {args.out}")


if __name__ == "__main__":
    main()
