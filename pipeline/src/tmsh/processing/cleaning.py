"""Cleaning and normalisation utilities for tmsh and iRule documentation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from bs4 import BeautifulSoup, NavigableString

IGNORED_SNIPPETS = (
    "COPYRIGHT",
    "If you are looking to move beyond–or simply bypass–the theory",
)

SECTION_HEADINGS = {
    "name": {"h1", "h2", "h3"},
    "module": {"h2", "h3"},
    "syntax": {"h2", "h3"},
    "description": {"h2", "h3"},
    "examples": {"h2", "h3"},
    "options": {"h2", "h3"},
    "see also": {"h2", "h3"},
}


@dataclass
class SyntaxSection:
    """Structured domain specific slice of documentation."""

    name: str
    module: Optional[str]
    syntax: Optional[str]
    description: str
    examples: List[str] = field(default_factory=list)
    options: List[str] = field(default_factory=list)
    see_also: List[str] = field(default_factory=list)
    domain: str = "tmsh"

    def to_record(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "module": self.module,
            "syntax": self.syntax,
            "description": self.description,
            "examples": self.examples,
            "options": self.options,
            "see_also": self.see_also,
            "domain": self.domain,
        }


def clean_html_fragment(html: str) -> str:
    """Return plain text with boilerplate removed."""

    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()

    text = soup.get_text("\n")
    for snippet in IGNORED_SNIPPETS:
        text = text.replace(snippet, "")

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def extract_syntax_sections(html: str, domain: str = "tmsh") -> List[SyntaxSection]:
    """Attempt to extract structured syntax blocks from an HTML fragment."""

    soup = BeautifulSoup(html, "html.parser")
    sections: List[SyntaxSection] = []

    current: Dict[str, object] = {"name": None, "module": None, "syntax": None, "description": []}
    examples: List[str] = []
    options: List[str] = []
    see_also: List[str] = []

    def flush_current() -> None:
        if current["name"] and current["description"]:
            sections.append(
                SyntaxSection(
                    name=str(current["name"]),
                    module=str(current["module"]) if current["module"] else None,
                    syntax=str(current["syntax"]) if current["syntax"] else None,
                    description="\n".join(str(x) for x in current["description"]).strip(),
                    examples=list(examples),
                    options=list(options),
                    see_also=list(see_also),
                    domain=domain,
                )
            )
        current["name"] = None
        current["module"] = None
        current["syntax"] = None
        current["description"] = []
        examples.clear()
        options.clear()
        see_also.clear()

    for element in soup.find_all(True, recursive=False):
        if isinstance(element, NavigableString):
            continue

        heading = element.name or ""
        text = element.get_text("\n").strip()
        if not text:
            continue

        lowered = text.lower()
        if heading in SECTION_HEADINGS.get("name", set()) and not current["name"]:
            current["name"] = text
            continue
        if heading in SECTION_HEADINGS.get("module", set()) and "module" in lowered:
            current["module"] = text
            continue
        if heading in SECTION_HEADINGS.get("syntax", set()) and "syntax" in lowered:
            current["syntax"] = text
            continue
        if heading in SECTION_HEADINGS.get("examples", set()) and "example" in lowered:
            examples.append(text)
            continue
        if heading in SECTION_HEADINGS.get("options", set()) and "option" in lowered:
            options.append(text)
            continue
        if heading in SECTION_HEADINGS.get("see also", set()) and "see also" in lowered:
            see_also.append(text)
            continue

        if current["name"] is None:
            continue

        current["description"].append(text)

    flush_current()
    return sections


def aggregate_tmsh_and_irule_sections(
    html_fragments: Iterable[str], *, domain: str = "tmsh"
) -> List[Dict[str, object]]:
    """Aggregate structured sections from multiple fragments."""

    records: List[Dict[str, object]] = []
    for fragment in html_fragments:
        records.extend(
            section.to_record()
            for section in extract_syntax_sections(fragment, domain=domain)
        )
    return records
