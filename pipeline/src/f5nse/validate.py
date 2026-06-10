from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from pathlib import Path

import tyro

LOGGER = logging.getLogger("f5nse.validate")


@dataclass
class ValidateArgs:
    graded_path: Path = Path("data/datasets/qa_graded.jsonl")
    min_records: int = 1
    expect_scores: bool = True
    require_context: bool = True
    min_overall: float | None = None


def load_records(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def run_validate(args: ValidateArgs) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if not args.graded_path.exists():
        LOGGER.error("Graded dataset %s not found.", args.graded_path)
        return
    totals = 0
    overall_scores = []
    missing_keys = set()
    for record in load_records(args.graded_path):
        totals += 1
        judge = record.get("judge", {})
        overall_recorded = False
        if args.expect_scores:
            if isinstance(judge, dict):
                scores = judge.get("scores")
                if isinstance(scores, dict):
                    overall = scores.get("overall")
                    if overall is not None:
                        try:
                            overall_val = float(overall)
                            overall_scores.append(overall_val)
                            overall_recorded = True
                        except (TypeError, ValueError):
                            missing_keys.add("scores.overall")
                else:
                    missing_keys.add("scores")
            root_overall = record.get("overall")
            if not overall_recorded and root_overall is not None:
                try:
                    overall_scores.append(float(root_overall))
                except (TypeError, ValueError):
                    missing_keys.add("overall")
        question = record.get("question") or record.get("prompt")
        if question is None:
            missing_keys.add("question")
        answer = record.get("answer") or record.get("completion")
        if answer is None:
            missing_keys.add("answer")
        context = record.get("context")
        if args.require_context and context is None:
            missing_keys.add("context")
    if totals < args.min_records:
        LOGGER.error("Only %s records found (minimum required %s).", totals, args.min_records)
        return
    LOGGER.info("Validated %s records in %s", totals, args.graded_path)
    if missing_keys:
        LOGGER.warning("Missing expected fields: %s", ", ".join(sorted(missing_keys)))
    if overall_scores:
        mean_score = statistics.fmean(overall_scores)
        median_score = statistics.median(overall_scores)
        LOGGER.info("Overall score mean %.4f median %.4f", mean_score, median_score)
        if args.min_overall is not None and mean_score < args.min_overall:
            LOGGER.warning(
                "Mean overall score %.4f below threshold %.4f", mean_score, args.min_overall
            )


def main() -> None:
    args = tyro.cli(ValidateArgs)
    run_validate(args)


if __name__ == "__main__":
    main()
