"""Normalize tmsh command docs (extracted from the PDF) into a structured schema.

Input:  tmos/tmsh_commands_raw.json (produced by extract_tmsh_schema.py)
Output: tmos/tmsh_schema.json with normalized fields ready for downstream use.

Normalization performed:
- Cleans page-break artifacts (single-letter lines, stray "R", empty padding).
- Drops COPYRIGHT sections and copyright boilerplate sentences.
- Splits SYNTAX into a list of variants.
- Converts OPTIONS into a list of {name, signature, description}.
- Collapses DESCRIPTION and EXAMPLES into concise strings/lists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent
RAW_PATH = ROOT / "tmsh_commands_raw.json"
OUT_PATH = ROOT / "tmsh_schema.json"

# Phrases that indicate boilerplate we should drop entirely.
COPYRIGHT_PHRASES = [
    "No part of this program may be reproduced or transmitted in any form",
    "without the express written permission of F5 Networks",
    "Copyright",
    "BIG-IP",
]

# Words that should not be treated as option names when they appear at the start
# of an OPTIONS line (they are usually description sentences).
OPTION_STOPWORDS = {
    "Specifies",
    "The",
    "This",
    "These",
    "If",
    "When",
    "Where",
    "Note:",
    "Examples",
    "Example",
    "Displays",
    "Creates",
    "Deletes",
    "Returns",
    "Save",
    "Runs",
    "Restarts",
    "Stops",
    "Starts",
    "Sets",
    "Shows",
    "Use",
}

OPTION_RE = re.compile(r"^([a-zA-Z0-9][\w\-/]*)(?:\s+(.*))?$")


def clean_lines(text: str) -> List[str]:
    """Trim whitespace, drop empty/boilerplate/page artifacts."""
    lines: List[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if len(s) == 1 and not s.isalnum():
            continue
        if s in {"R", "Page"}:
            continue
        if any(phrase in s for phrase in COPYRIGHT_PHRASES):
            continue
        lines.append(s)
    return lines


def parse_options(text: str) -> List[Dict[str, str]]:
    """Parse OPTIONS section into a list of option dicts."""
    lines = clean_lines(text)
    options: List[Dict[str, str]] = []
    current: Dict[str, str] | None = None

    def start_option(name: str, signature: str) -> None:
        nonlocal current
        current = {"name": name, "signature": signature.strip(), "description": ""}
        options.append(current)

    for line in lines:
        if line.lower() == "options:":
            continue

        match = OPTION_RE.match(line)
        if match:
            name, signature = match.groups()
            if name and name not in OPTION_STOPWORDS:
                start_option(name, signature or "")
                continue

        if current:
            if current["description"]:
                current["description"] += " " + line
            else:
                current["description"] = line

    # Final tidy-up: strip descriptions.
    for opt in options:
        opt["description"] = opt["description"].strip()
    return options


def normalize_command(cmd: Dict[str, object]) -> Dict[str, object]:
    sections: Dict[str, str] = cmd.get("sections", {})  # type: ignore

    syntax = clean_lines(sections.get("SYNTAX", "")) if isinstance(sections, dict) else []
    description = " ".join(clean_lines(sections.get("DESCRIPTION", ""))) if isinstance(sections, dict) else ""
    examples = clean_lines(sections.get("EXAMPLES", "")) if isinstance(sections, dict) else []
    options = parse_options(sections.get("OPTIONS", "")) if isinstance(sections, dict) else []

    return {
        "id": cmd.get("id"),
        "module_path": cmd.get("module_path", []),
        "command": cmd.get("command"),
        "summary": cmd.get("summary", ""),
        "syntax": syntax,
        "description": description,
        "examples": examples,
        "options": options,
    }


def main() -> None:
    raw_data = json.loads(RAW_PATH.read_text())
    normalized = [normalize_command(cmd) for cmd in raw_data]
    OUT_PATH.write_text(json.dumps(normalized, indent=2))
    print(f"Wrote normalized tmsh schema: {OUT_PATH} ({len(normalized)} commands)")


if __name__ == "__main__":
    main()
