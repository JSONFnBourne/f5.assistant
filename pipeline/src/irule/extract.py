from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .rulebook import load_event_order


LOGGER = logging.getLogger("irule.extract")

DISCLAIMER_SNIPPET = (
    "The BIG-IP API Reference documentation contains community-contributed content."
)


def strip_disclaimer(text: str) -> str:
    if DISCLAIMER_SNIPPET in text:
        return text.split(DISCLAIMER_SNIPPET, 1)[0].strip()
    return text


def load_clean_index(clean_dir: Path) -> Dict[str, dict]:
    index: Dict[str, dict] = {}
    for path in clean_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        url = data.get("url")
        if url:
            index[url] = data
    return index


def parse_bullet_entries(section_text: str) -> List[dict]:
    entries: List[dict] = []
    current_name: Optional[str] = None
    current_desc: List[str] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("-"):
            if current_name:
                entries.append({"name": current_name, "summary": " ".join(current_desc).strip()})
            content = line[1:].strip()
            if " - " in content:
                name, desc = content.split(" - ", 1)
                current_name = name.strip()
                current_desc = [desc.strip()]
            else:
                current_name = content
                current_desc = []
        else:
            if current_name:
                current_desc.append(line)
    if current_name:
        entries.append({"name": current_name, "summary": " ".join(current_desc).strip()})
    return entries


def path_for_entity(name: str) -> str:
    return name.replace("::", "__") + ".html"


def parse_examples(text: str) -> List[str]:
    examples: List[str] = []
    lines = text.splitlines()
    collecting = False
    current: List[str] = []
    for line in lines:
        if line.startswith("## "):
            if collecting:
                if current:
                    examples.append("\n".join(current).strip())
                current = []
            collecting = line.strip().lower() == "## examples"
            continue
        if collecting:
            current.append(line)
    if collecting and current:
        examples.append("\n".join(current).strip())
    cleaned = [strip_disclaimer(ex).strip() for ex in examples if ex.strip()]
    return cleaned


def parse_valid_events(text: str) -> List[str]:
    matches = re.search(r"Valid Events\s*:?\s*(.*)", text, re.IGNORECASE)
    if not matches:
        return []
    line = matches.group(1)
    line = line.replace("and", ",")
    parts = [part.strip().rstrip('.') for part in line.split(',')]
    return [part for part in parts if part]


def extract_section_by_heading(text: str, headings: Iterable[str]) -> str:
    """Extract a section body in cleaned text delimited by '## <Heading>'.

    - `headings` should be lowercase heading names to match (e.g., ["related information"]).
    - Returns the section body until the next '## ' heading or end of text.
    """
    target = {h.strip().lower() for h in headings}
    lines = text.splitlines()
    buf: List[str] = []
    collecting = False
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("## "):
            name = line[3:].strip().lower()
            if collecting:
                break
            collecting = name in target
            continue
        if collecting:
            buf.append(line)
    body = "\n".join(buf).strip()
    return strip_disclaimer(body)


def ensure_module_page(module: str, index: Dict[str, dict]) -> dict:
    url = f"https://clouddocs.f5.com/api/irules/{module}.html"
    if url not in index:
        raise FileNotFoundError(f"Module page {url} not found in clean index")
    return index[url]


def build_entity_records(module: str, clean_dir: Path, event_order: Dict[str, dict]) -> List[dict]:
    index = load_clean_index(clean_dir)
    module_data = ensure_module_page(module, index)
    module_text = strip_disclaimer(module_data.get("text", ""))

    lines = module_text.splitlines()
    state = None
    sections: Dict[str, List[str]] = {"description": []}
    for line in lines:
        line = line.rstrip()
        if line.startswith("## "):
            state = line[3:].strip().lower()
            if state not in sections:
                sections[state] = []
            continue
        if state:
            sections[state].append(line)

    description = "\n".join(sections.get("description", [])).strip()
    if not description:
        description = strip_disclaimer((module_data.get("sections", {}) or {}).get("description", ""))
    # Tolerate headings labelled either "Command List" or "Commands"
    command_section_lines = sections.get("command list", []) or sections.get("commands", [])
    command_entries = parse_bullet_entries("\n".join(command_section_lines))
    # Fallback to the cleaner's structured sections if needed
    if not command_entries:
        structured = (module_data.get("sections", {}) or {}).get("available_commands", "")
        if structured:
            # available_commands is a simple newline list without dashes; parse per line
            entries: List[dict] = []
            for raw in structured.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if " - " in line:
                    name, desc = line.split(" - ", 1)
                    entries.append({"name": name.strip(), "summary": desc.strip()})
                else:
                    entries.append({"name": line, "summary": ""})
            command_entries = entries

    # Tolerate headings labelled either "Event List" or "Events"
    event_section_lines = sections.get("event list", []) or sections.get("events", [])
    event_entries = parse_bullet_entries("\n".join(event_section_lines))
    if not event_entries:
        structured = (module_data.get("sections", {}) or {}).get("associated_events", "")
        if structured:
            entries: List[dict] = []
            for raw in structured.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if " - " in line:
                    name, desc = line.split(" - ", 1)
                    entries.append({"name": name.strip(), "summary": desc.strip()})
                else:
                    entries.append({"name": line, "summary": ""})
            event_entries = entries
    module_related_info = "\n".join(sections.get("related information", [])).strip()

    entities: List[dict] = []

    base_url = module_data.get("url")
    entities.append(
        {
            "module": module,
            "name": module,
            "kind": "module",
            "description": description,
            **({"related_information": module_related_info} if module_related_info else {}),
            "source_url": base_url,
        }
    )

    for entry in command_entries:
        name = entry.get("name", "").strip()
        if not name:
            continue
        summary = entry.get("summary", "").strip()
        command_url = f"https://clouddocs.f5.com/api/irules/{path_for_entity(name)}"
        detail = index.get(command_url)
        description_detail = None
        syntax = None
        examples: List[str] = []
        valid_events: List[str] = []
        if detail:
            detail_sections = detail.get("sections", {})
            description_detail = strip_disclaimer(detail_sections.get("description", "") or detail.get("text", ""))
            syntax = detail_sections.get("syntax")
            examples = parse_examples(detail.get("text", ""))
            valid_events = parse_valid_events(detail.get("text", ""))
            related_information = extract_section_by_heading(detail.get("text", ""), ("related information",))
        entity = {
            "module": module,
            "name": name,
            "kind": "command",
            "summary": summary,
            "description": description_detail or summary,
            "syntax": syntax,
            "examples": examples,
            "valid_events": valid_events,
            **({"related_information": related_information} if detail and related_information else {}),
            "source_url": command_url if detail else base_url,
        }
        entities.append(entity)

    for entry in event_entries:
        name = entry.get("name", "").strip()
        if not name:
            continue
        summary = entry.get("summary", "").strip()
        event_url = f"https://clouddocs.f5.com/api/irules/{path_for_entity(name)}"
        detail = index.get(event_url)
        description_detail = None
        examples: List[str] = []
        if detail:
            detail_sections = detail.get("sections", {})
            description_detail = strip_disclaimer(detail_sections.get("description", "") or detail.get("text", ""))
            examples = parse_examples(detail.get("text", ""))
            related_information = extract_section_by_heading(detail.get("text", ""), ("related information",))
        order_meta = event_order.get(name, {})
        entity = {
            "module": module,
            "name": name,
            "kind": "event",
            "summary": summary,
            "description": description_detail or summary,
            "source_url": event_url if detail else base_url,
            "event_order": order_meta,
            "examples": examples,
            **({"related_information": related_information} if detail and related_information else {}),
        }
        entities.append(entity)

    return entities


@dataclass
class ExtractArgs:
    module: str
    clean_dir: Path = Path("data/clean")
    output_path: Optional[Path] = None
    event_order_path: Optional[Path] = Path("irules_https_event_order.jsonl")


def run_extract(args: ExtractArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    module = args.module.strip().upper()
    if not module:
        raise ValueError("Module name must be provided")
    event_order = load_event_order(args.event_order_path)
    entities = build_entity_records(module, args.clean_dir, event_order)
    output_path = args.output_path or Path(f"data/entities/{module.lower()}.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for entity in entities:
            fh.write(json.dumps(entity, ensure_ascii=False) + "\n")
    LOGGER.info("Wrote %s entities for module %s to %s", len(entities), module, output_path)


@dataclass
class ExtractAllArgs:
    clean_dir: Path = Path("data/clean")
    output_dir: Path = Path("data/entities")
    event_order_path: Optional[Path] = Path("irules_https_event_order.jsonl")
    modules: Optional[List[str]] = None  # Optional explicit list to restrict extraction
    aggregate_path: Optional[Path] = None  # If set, also write a combined JSONL here


def discover_modules_from_index(index: Dict[str, dict]) -> List[str]:
    pattern = re.compile(r"https://clouddocs\.f5\.com/api/irules/([A-Z0-9]+)\.html$")
    mods: set[str] = set()
    for url in index.keys():
        m = pattern.search(url)
        if m:
            mods.add(m.group(1))
    return sorted(mods)


def run_extract_all(args: ExtractAllArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    index = load_clean_index(args.clean_dir)
    if not index:
        raise FileNotFoundError(f"No cleaned documents found in {args.clean_dir}")
    modules = [m.strip().upper() for m in (args.modules or []) if m.strip()] or discover_modules_from_index(index)
    if not modules:
        raise RuntimeError("No modules discovered to extract.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    event_order = load_event_order(args.event_order_path)
    total = 0
    agg_fh = args.aggregate_path.open("w", encoding="utf-8") if args.aggregate_path else None
    try:
        for mod in modules:
            try:
                entities = build_entity_records(mod, args.clean_dir, event_order)
            except FileNotFoundError as exc:
                LOGGER.warning("Skipping module %s: %s", mod, exc)
                continue
            out_path = args.output_dir / f"{mod.lower()}.jsonl"
            with out_path.open("w", encoding="utf-8") as fh:
                for ent in entities:
                    line = json.dumps(ent, ensure_ascii=False)
                    fh.write(line + "\n")
                    if agg_fh:
                        agg_fh.write(line + "\n")
                    total += 1
            LOGGER.info("Wrote %s entities for module %s to %s", len(entities), mod, out_path)
    finally:
        if agg_fh:
            agg_fh.close()
    if args.aggregate_path:
        LOGGER.info("Aggregate entity file written to %s (total records %s)", args.aggregate_path, total)


def main() -> None:
    import tyro

    args = tyro.cli(ExtractArgs)
    run_extract(args)


if __name__ == "__main__":
    main()
