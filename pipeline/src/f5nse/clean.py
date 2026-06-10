from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import tyro
from bs4 import BeautifulSoup, NavigableString

LOGGER = logging.getLogger("f5nse.clean")


def yield_raw_documents(raw_dir: Path) -> Iterator[tuple[Path, dict]]:
    for path in sorted(raw_dir.glob("*.json")):
        try:
            yield path, json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            LOGGER.warning("Skipping %s: %s", path, exc)


def extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text():
        return h1.get_text().strip()
    return ""


DISALLOWED_SNIPPETS = [
    (
        "The BIG-IP API Reference documentation contains community-contributed content. "
        "F5 does not monitor or control community code contributions. We make no guarantees "
        "or warranties regarding the available code, and it may contain errors, defects, "
        "bugs, inaccuracies, or security vulnerabilities. Your access to and use of any code "
        "available in the BIG-IP API reference guides is solely at your own risk."
    )
]


def sanitize_text(text: str) -> str:
    for snippet in DISALLOWED_SNIPPETS:
        text = text.replace(snippet, "")
    text = text.replace("\xa0", " ").replace("¶", "")
    # Collapse multiple spaces while preserving newlines
    lines = []
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


CONTENT_SELECTORS = [
    "article",
    "main",
    "div[role=main]",
    "section[role=main]",
    "div.section",
    "div.content",
    "div.body",
]


def select_content_root(soup: BeautifulSoup) -> BeautifulSoup:
    for selector in CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node:
            return BeautifulSoup(str(node), "html.parser")
    return soup


def remove_boilerplate(soup: BeautifulSoup) -> None:
    boilerplate_phrases = [
        "The BIG-IP API Reference documentation contains community-contributed content.",
        "The links to the sample code below are remnants of the old DevCentral wiki",
    ]
    for phrase in boilerplate_phrases:
        for text_node in list(soup.find_all(string=lambda s, p=phrase: s and p in s)):
            parent = text_node.parent
            target = parent
            while target and target.name not in {"p", "div", "section"}:
                target = target.parent
            if target:
                target.decompose()
            else:
                text_node.extract()
    for warning in soup.find_all("div", class_="admonition"):
        if warning.find(string=lambda s: s and "DevCentral wiki" in s):
            warning.decompose()
    for line_block in soup.find_all("div", class_="line-block"):
        if not line_block.get_text(strip=True):
            line_block.decompose()


def soup_to_text(soup: BeautifulSoup, preserve_headings: bool) -> str:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        tag.decompose()

    lines: list[str] = []
    for element in soup.stripped_strings:
        lines.append(element)
    text = "\n".join(lines)

    if not preserve_headings:
        return text

    # Re-run with heading awareness for readability.
    lines = []
    for elem in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "code"]):
        content = elem.get_text(" ", strip=True)
        if not content:
            continue
        if elem.name.startswith("h"):
            level = int(elem.name[1])
            prefix = "#" * level
            lines.append(f"{prefix} {content}")
        elif elem.name == "li":
            lines.append(f"- {content}")
        else:
            lines.append(content)
    if not lines:
        return text
    return "\n".join(lines)


def extract_section_after_heading(soup: BeautifulSoup, heading_terms: Sequence[str]) -> str:
    def matches_heading(tag) -> bool:
        if not tag or tag.name not in {"h1", "h2", "h3", "h4"}:
            return False
        content = tag.get_text(" ", strip=True).lower().replace("¶", "")
        return any(term in content for term in heading_terms)

    header = soup.find(matches_heading)
    if not header:
        return ""
    collected: list[str] = []
    for sibling in header.next_siblings:
        if getattr(sibling, "name", None) in {"h1", "h2", "h3"}:
            break
        if isinstance(sibling, NavigableString):
            value = str(sibling).strip()
            if value:
                collected.append(value)
        else:
            value = sibling.get_text(" ", strip=True)
            if value:
                collected.append(value)
    return "\n".join(collected)


def extract_available_commands(soup: BeautifulSoup) -> list[str]:
    strong = soup.find("strong", string=lambda s: s and "Available Commands" in s)
    if not strong:
        return []
    commands: list[str] = []
    parent = strong.parent
    for sibling in parent.next_siblings:
        name = getattr(sibling, "name", None)
        if name and name.startswith("h"):
            break
        if name == "div" and "listbykeyword" in (sibling.get("class") or []):
            for li in sibling.find_all("li"):
                link = li.find("a")
                label = link.get_text(" ", strip=True) if link else ""
                trailing = li.get_text(" ", strip=True)
                if label and trailing:
                    # Remove duplicated label text from trailing part
                    description = trailing.replace(label, "", 1).strip(" -")
                    description = " ".join(description.split())
                    commands.append(f"{label} - {description}" if description else label)
                elif trailing:
                    commands.append(" ".join(trailing.split()))
            continue
        if name == "div" and "line-block" in (sibling.get("class") or []):
            for line in sibling.find_all("div", class_="line"):
                text = " ".join(line.get_text(" ", strip=True).split())
                if text:
                    commands.append(text)
            continue
        if name == "ul":
            for li in sibling.find_all("li"):
                text = li.get_text(" ", strip=True)
                if text:
                    commands.append(text)
            continue
        if name in {"p"} and sibling.find("strong"):
            # reached next bold section (e.g., Sample Code)
            break
    return commands


def is_irules_url(url: str | None) -> bool:
    return bool(url and "clouddocs.f5.com/api/irules" in url)


def extract_irule_sections(soup: BeautifulSoup) -> dict[str, str]:
    sections = {
        "description": extract_section_after_heading(soup, ("description",)),
        "command_list": extract_section_after_heading(soup, ("command list", "commands")),
        "associated_events": extract_section_after_heading(soup, ("associated events",)),
        "syntax": extract_section_after_heading(soup, ("syntax",)),
    }
    commands = extract_available_commands(soup)
    if commands:
        sections["available_commands"] = "\n".join(commands)
    cleaned = {}
    for key, value in sections.items():
        sanitized = sanitize_text(value)
        if sanitized:
            cleaned[key] = sanitized
    return cleaned


def simple_dedupe_key(text: str, length: int = 40) -> str:
    normalized = text.lower().replace(" ", "")
    return normalized[:length]


@dataclass
class CleanArgs:
    raw_dir: Path = Path("data/raw")
    output_dir: Path = Path("data/clean")
    preserve_headings: bool = True
    min_characters: int = 200
    dedupe: bool = True
    overwrite: bool = False


def run_clean(args: CleanArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    seen_keys: set[str] = set()
    processed = 0
    for path, record in yield_raw_documents(args.raw_dir):
        raw_soup = BeautifulSoup(record.get("html", ""), "html.parser")
        content_soup = select_content_root(raw_soup)
        remove_boilerplate(content_soup)
        text = soup_to_text(content_soup, args.preserve_headings)
        text = sanitize_text(text)
        if len(text) < args.min_characters:
            LOGGER.debug("Skipping %s: below min characters", record.get("url"))
            continue
        dedupe_key = simple_dedupe_key(text)
        if args.dedupe and dedupe_key in seen_keys:
            LOGGER.debug("Duplicate detected for %s", record.get("url"))
            continue
        seen_keys.add(dedupe_key)

        sections = extract_irule_sections(content_soup) if is_irules_url(record.get("url")) else {}

        output = {
            "url": record.get("url"),
            "source_root": record.get("source_root"),
            "retrieved_at": record.get("retrieved_at"),
            "depth": record.get("depth"),
            "title": extract_title(raw_soup),
            "text": text,
            "char_len": len(text),
        }
        if sections:
            output["sections"] = sections
        out_path = args.output_dir / Path(path.name)
        if out_path.exists() and not args.overwrite:
            LOGGER.debug("Skipping existing %s", out_path)
            continue
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
        processed += 1
    LOGGER.info("Cleaned %s documents into %s", processed, args.output_dir)


def main() -> None:
    args = tyro.cli(CleanArgs)
    run_clean(args)


if __name__ == "__main__":
    main()
