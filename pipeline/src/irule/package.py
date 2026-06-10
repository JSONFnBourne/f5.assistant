from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import tyro

LOGGER = logging.getLogger("irule.package")


def summarize_jsonl(path: Path, limit: int | None = 100) -> dict:
    stats = {"records": 0, "sample_questions": []}
    with path.open("r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh):
            if not line.strip():
                continue
            stats["records"] += 1
            if len(stats["sample_questions"]) < 5:
                try:
                    data = json.loads(line)
                    question = data.get("question") or data.get("prompt")
                    if question:
                        stats["sample_questions"].append(question[:120])
                except json.JSONDecodeError:
                    continue
            if limit and idx >= limit:
                break
    return stats


@dataclass
class PackageArgs:
    input_path: Path = Path("data/chunks/chunks.jsonl")
    additional_file: Path | None = Path("data/datasets/qa_raw.jsonl")
    output_path: Path = Path("data/external/qa_package.zip")
    summary_path: Path | None = None
    limit_records: int | None = None


def create_package(args: PackageArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(args.input_path, arcname=args.input_path.name)
        LOGGER.info("Added %s", args.input_path)
        if args.additional_file and args.additional_file.exists():
            zf.write(args.additional_file, arcname=args.additional_file.name)
            LOGGER.info("Added %s", args.additional_file)
        summary = summarize_jsonl(args.additional_file or args.input_path, args.limit_records)
        summary_path = args.summary_path or Path("summary.json")
        summary_path = summary_path if summary_path.is_absolute() else Path(summary_path.name)
        zf.writestr(summary_path.name, json.dumps(summary, indent=2, ensure_ascii=False))
        LOGGER.info("Embedded summary metadata %s", summary_path.name)
    LOGGER.info("Created export package at %s", args.output_path)


def main() -> None:
    args = tyro.cli(PackageArgs)
    create_package(args)


if __name__ == "__main__":
    main()
