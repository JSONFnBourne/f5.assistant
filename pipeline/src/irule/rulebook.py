from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Set


LOGGER = logging.getLogger("irule.rulebook")


COMMAND_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_:]*::[A-Za-z0-9_:-]+)")
EVENT_DECL_PATTERN = re.compile(r"\bwhen\s+([A-Z][A-Z0-9_]+)\b")

SYMBOL_OPERATORS = {
    "~",
    "!",
    "*",
    "/",
    "%",
    "+",
    "-",
    "<<",
    ">>",
    "<",
    ">",
    "<=",
    ">=",
    "==",
    "!=",
    "eq",
    "ne",
    "&",
    "^",
    "|",
    "&&",
    "||",
}

WORD_OPERATORS = {
    "contains",
    "ends_with",
    "equals",
    "matches_glob",
    "matches_regex",
    "starts_with",
    "switch",
    "and",
    "not",
    "or",
}
DEFAULT_OPERATORS = WORD_OPERATORS.union(SYMBOL_OPERATORS)


@dataclass
class Rulebook:
    commands: Set[str]
    events: Set[str]
    operators: Set[str]
    event_order: Dict[str, dict]


def extract_command_tokens(text: str) -> Set[str]:
    return set(match.group(1) for match in COMMAND_PATTERN.finditer(text))


def discover_events(text: str) -> Set[str]:
    return set(EVENT_DECL_PATTERN.findall(text))


def extract_events_from_text(text: str, known_events: Set[str]) -> Set[str]:
    return {event for event in known_events if event in text}


def extract_operator_tokens(text: str, known_operators: Set[str]) -> Set[str]:
    tokens: Set[str] = set()
    lowered = text.lower()
    for op in WORD_OPERATORS:
        if op in known_operators and re.search(rf"\b{re.escape(op)}\b", lowered):
            tokens.add(op)
    for op in SYMBOL_OPERATORS:
        if op in known_operators and op in text:
            tokens.add(op)
    return tokens


def load_rulebook(path: Path) -> Rulebook:
    if not path.exists():
        return Rulebook(set(), set(), set(DEFAULT_OPERATORS), {})
    data = json.loads(path.read_text(encoding="utf-8"))
    # Backward compatibility with legacy list format
    if isinstance(data, list):
        return Rulebook(set(map(str, data)), set(), set(DEFAULT_OPERATORS), {})
    commands = {str(tok) for tok in data.get("commands", [])}
    events = {str(tok) for tok in data.get("events", [])}
    operators = {str(tok) for tok in data.get("operators", [])}
    if not operators:
        operators = set(DEFAULT_OPERATORS)
    event_order = {str(k): v for k, v in (data.get("event_order", {}) or {}).items()}
    return Rulebook(commands, events, operators, event_order)


def write_rulebook(rulebook: Rulebook, path: Path) -> None:
    payload: Dict[str, Iterable[str]] = {
        "commands": sorted(rulebook.commands),
        "events": sorted(rulebook.events),
        "operators": sorted(rulebook.operators),
        "event_order": rulebook.event_order,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@dataclass
class RulebookArgs:
    chunks_path: Path = Path("data/chunks/chunks.jsonl")
    output_path: Path = Path("data/rulebook/command_tokens.json")
    event_order_path: Optional[Path] = Path("irules_https_event_order.jsonl")


def load_event_order(path: Optional[Path]) -> Dict[str, dict]:
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        LOGGER.warning("Event order file %s not found; skipping event ordering data.", path)
        return {}
    order_map: Dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = str(record.get("event", "")).strip()
            if not event:
                continue
            order_map[event] = record
    LOGGER.info("Loaded %s event ordering entries from %s", len(order_map), path)
    return order_map


def run_rulebook(args: RulebookArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if not args.chunks_path.exists():
        LOGGER.error("Chunks file %s not found", args.chunks_path)
        return
    commands: Set[str] = set()
    events: Set[str] = set()
    with args.chunks_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunk_text = chunk.get("chunk", "")
            commands.update(extract_command_tokens(chunk_text))
            events.update(discover_events(chunk_text))
    if not commands:
        LOGGER.warning("No command tokens discovered in %s", args.chunks_path)
    event_order = load_event_order(args.event_order_path)
    rulebook = Rulebook(commands, events, set(DEFAULT_OPERATORS), event_order)
    write_rulebook(rulebook, args.output_path)
    LOGGER.info(
        "Wrote %s commands, %s events, %s operators, %s event-order entries to %s",
        len(rulebook.commands),
        len(rulebook.events),
        len(rulebook.operators),
        len(rulebook.event_order),
        args.output_path,
    )


def main() -> None:
    args = RulebookArgs()
    run_rulebook(args)


if __name__ == "__main__":
    main()
