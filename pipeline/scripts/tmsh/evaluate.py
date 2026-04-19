#!/usr/bin/env python
"""Evaluate a fine-tuned tmsh model."""
from __future__ import annotations

import argparse

from tmsh.evaluation.evaluator import EvaluationConfig, evaluate_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="Path to the fine-tuned model or adapter")
    parser.add_argument("dataset", help="Path to the evaluation dataset JSONL")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = EvaluationConfig(
        model_path=args.model,
        dataset_path=args.dataset,
        max_new_tokens=args.max_new_tokens,
    )
    metrics = evaluate_model(config)
    for key, value in metrics.items():
        print(f"{key}: {value:.4f}")


if __name__ == "__main__":
    main()
