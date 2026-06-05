#!/usr/bin/env python3
"""Re-judge cached eval candidates with a stronger judge served by local Ollama.

Holds the fine-tuned model's generations fixed (reads the candidates cache
written by `irule.cli evaluate`) and varies only the judge, so the result
isolates judge quality from model quality. Default judge: qwen2.5:7b, which is
far stronger at instruction-following / JSON than the Llama-3.2-3B self-judge.

Usage:
    python scripts/rejudge_ollama.py \
        --candidates data/models/eval_candidates.jsonl \
        --judge-model qwen2.5:7b \
        --out data/models/eval_rejudge_qwen7b.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import urllib.request
from pathlib import Path

SYSTEM = (
    "You are a fair, knowledgeable grader of short technical answers about F5 "
    "BIG-IP iRules. You are given a Question, a Reference answer, and a Candidate "
    "answer. Judge whether the Candidate is factually correct and consistent with "
    "the Reference and the question. Credit answers that are correct even if worded "
    "differently from the Reference; penalize wrong, empty, or off-topic answers. "
    'Output ONLY JSON: {"score": <float 0..1>, "verdict": "pass" or "fail", '
    '"reason": "<short>"}. verdict is "pass" when the candidate is substantially '
    "correct (score >= 0.7)."
)


def judge(host: str, model: str, q: str, r: str, c: str) -> dict:
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": f"Question: {q}\nReference answer: {r}\nCandidate answer: {c}\n\nGrade the candidate.",
                },
            ],
        }
    ).encode()
    req = urllib.request.Request(
        f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    content = json.load(urllib.request.urlopen(req, timeout=180))["message"]["content"]
    return json.loads(content)  # may raise; caller counts as parse failure


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default="data/models/eval_candidates.jsonl")
    ap.add_argument("--judge-model", default="qwen2.5:7b")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--out", default="data/models/eval_rejudge_qwen7b.jsonl")
    args = ap.parse_args()

    rows = [json.loads(line) for line in Path(args.candidates).open()]
    scores: list[float] = []
    parse_fail = 0
    results = []
    for r in rows:
        try:
            verdict = judge(
                args.host,
                args.judge_model,
                r.get("question", ""),
                r.get("reference", ""),
                r.get("candidate", ""),
            )
            s = float(verdict.get("score", 0.0))
        except Exception as exc:  # noqa: BLE001 - count any judge/parse error
            parse_fail += 1
            results.append({**r, "judge_error": str(exc)[:120]})
            continue
        scores.append(s)
        results.append({**r, "judge": verdict})

    Path(args.out).write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in results))

    n = len(scores)
    print(f"judge={args.judge_model}  candidates={len(rows)}  judged={n}  parse_failures={parse_fail}")
    if n:
        print(
            f"  mean_score={st.mean(scores):.3f}  median={st.median(scores):.3f}"
            f"  pass@0.70={sum(s >= 0.70 for s in scores)}/{n} ({sum(s >= 0.70 for s in scores)/n:.0%})"
            f"  pass@0.85={sum(s >= 0.85 for s in scores)}/{n} ({sum(s >= 0.85 for s in scores)/n:.0%})"
        )
    print(f"  wrote per-item results -> {args.out}")


if __name__ == "__main__":
    main()
