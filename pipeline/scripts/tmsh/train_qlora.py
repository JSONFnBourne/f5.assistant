#!/usr/bin/env python
"""Entry point to train the tmsh adapter with QLoRA."""
from __future__ import annotations

import argparse
from pathlib import Path

from tmsh.training.qlora import QLoRAConfig, train_qlora


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Path to the JSONL dataset")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/qlora"),
        help="Directory for model checkpoints",
    )
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accumulation", type=int, default=16)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = QLoRAConfig(
        data_path=args.dataset,
        output_dir=args.output_dir,
        max_steps=args.max_steps,
        batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accumulation,
    )
    trainer = train_qlora(config)
    trainer.train()
    trainer.save_model()


if __name__ == "__main__":
    main()
