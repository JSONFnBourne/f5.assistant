from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

import tyro

from . import (
    chunk,
    clean,
    evaluate,
    export,
    generate,
    grade,
    merge,
    package,
    scrape,
    serve,
    split,
    train,
    validate,
)

CommandEntry = tuple[type[object], Callable[[object], None], str]


def _build_commands() -> dict[str, CommandEntry]:
    return {
        "scrape": (
            scrape.ScrapeArgs,
            lambda parsed: asyncio.run(scrape.run_scraper(parsed)),
            "Scrape approved sources with TTL-aware crawler.",
        ),
        "clean": (
            clean.CleanArgs,
            clean.run_clean,
            "Clean and deduplicate raw HTML captures.",
        ),
        "chunk": (
            chunk.ChunkArgs,
            chunk.run_chunk,
            "Tokenize documents into overlapping chunks.",
        ),
        "generate": (
            generate.GenerateArgs,
            generate.run_generate,
            "Generate QA records from chunks using the generator model.",
        ),
        "grade": (
            grade.GradeArgs,
            grade.run_grade,
            "Judge QA pairs and retain high-confidence records.",
        ),
        "split": (
            split.SplitArgs,
            split.run_split,
            "Produce deterministic train/eval splits.",
        ),
        "train": (
            train.TrainArgs,
            train.run_train,
            "Fine-tune via QLoRA on curated data.",
        ),
        "evaluate": (
            evaluate.EvaluateArgs,
            evaluate.run_evaluate,
            "Run judge-based evaluation on the eval split.",
        ),
        "merge": (
            merge.MergeArgs,
            merge.run_merge,
            "Merge LoRA adapters into base weights.",
        ),
        "export": (
            export.ExportArgs,
            export.run_export,
            "Export merged weights to GGUF using llama.cpp tooling.",
        ),
        "serve": (
            serve.ServeArgs,
            serve.serve,
            "Launch FastAPI inference server backed by the fine-tuned model.",
        ),
        "package": (
            package.PackageArgs,
            package.create_package,
            "Bundle chunks/QA data for external grading or archiving.",
        ),
        "validate": (
            validate.ValidateArgs,
            validate.run_validate,
            "Verify schema and score statistics of graded datasets.",
        ),
    }


def _print_help(commands: dict[str, CommandEntry]) -> None:
    lines = ["Usage: python -m f5nse.cli <command> [--flags]\n", "Available commands:"]
    for name, (_, _, description) in commands.items():
        lines.append(f"  {name:<10} {description}")
    lines.append("\nUse 'python -m f5nse.cli <command> --help' for command-specific flags.")
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    commands = _build_commands()
    if not argv or argv[0] in ("-h", "--help"):
        _print_help(commands)
        return
    command_name, *command_args = argv
    if command_name not in commands:
        _print_help(commands)
        sys.exit(1)
    dataclass_type, runner, _ = commands[command_name]
    parsed_args = tyro.cli(dataclass_type, args=command_args)
    runner(parsed_args)


if __name__ == "__main__":
    main()
