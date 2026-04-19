from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import tyro

LOGGER = logging.getLogger("irule.split")


def load_records(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


@dataclass
class SplitArgs:
    graded_path: Path = Path("data/datasets/qa_graded.jsonl")
    train_path: Path = Path("data/datasets/train.jsonl")
    eval_path: Path = Path("data/datasets/eval.jsonl")
    train_ratio: float = 0.9
    seed: int = 42
    shuffle: bool = True


def run_split(args: SplitArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    records = load_records(args.graded_path)
    if not records:
        LOGGER.error("No graded records found at %s", args.graded_path)
        return
    if args.shuffle:
        random.Random(args.seed).shuffle(records)
    split_idx = int(len(records) * args.train_ratio)
    train_records = records[:split_idx]
    eval_records = records[split_idx:]
    args.train_path.parent.mkdir(parents=True, exist_ok=True)
    args.eval_path.parent.mkdir(parents=True, exist_ok=True)
    with args.train_path.open("w", encoding="utf-8") as fh:
        for record in train_records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    with args.eval_path.open("w", encoding="utf-8") as fh:
        for record in eval_records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    LOGGER.info(
        "Split dataset into %s train and %s eval records (ratio %.2f)",
        len(train_records),
        len(eval_records),
        args.train_ratio,
    )


def main() -> None:
    args = tyro.cli(SplitArgs)
    run_split(args)


if __name__ == "__main__":
    main()
