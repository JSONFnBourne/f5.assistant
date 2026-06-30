#!/usr/bin/env python3
"""Assemble BLIND judging packets from answers.jsonl (run after generation).

One packet per (qid, condition): the question, a reference distilled from the
retrieved F5 docs (the answer key — used to check grounding for RAG and to
fact-check for closed-book), and the 4 model answers ANONYMIZED to A/B/C/D with
a per-packet randomized mapping so the judge can't pattern-match model style.
The mapping is written separately for de-anonymization at analysis time.

GPU-free. Run from repo root after run_bench.py finishes:
  .venv/bin/python eval/bench/make_packets.py
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path

BENCH = Path(__file__).resolve().parent
MODELS = ["qwen2.5:14b", "qwen3:14b", "phi4", "phi4-reasoning"]
LETTERS = ["A", "B", "C", "D"]

def perm(seed: str):
    """Deterministic per-packet model->letter permutation (reproducible, no RNG state)."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    models = MODELS[:]
    order = []
    while models:
        i = h % len(models)
        order.append(models.pop(i))
        h //= max(len(models), 1) + 1
    return {m: LETTERS[i] for i, m in enumerate(order)}  # model -> letter

def reference(docs, n=6):
    out = []
    for d in docs[:n]:
        out.append(f"- [{d.get('title')}] {(d.get('content') or '')[:350].strip()}")
    return "\n".join(out)

def main():
    ans = [json.loads(l) for l in open(BENCH / "answers.jsonl") if l.strip()]
    qs = {q["id"]: q for q in (json.loads(l) for l in open(BENCH / "benchmark.jsonl") if l.strip())}
    ret = {r["id"]: r for r in json.load(open(BENCH / "retrieval.json"))}
    # index answers: (qid, condition, model) -> record
    A = {(a["model"], a["condition"], a["qid"]): a for a in ans}

    packets, mapping = [], {}
    for cond in ("rag", "cb"):
        for qid, q in qs.items():
            recs = {m: A.get((m, cond, qid)) for m in MODELS}
            if any(r is None for r in recs.values()):
                continue  # incomplete — skip until all 4 present
            m2l = perm(f"{qid}|{cond}")
            answers = {m2l[m]: (recs[m]["answer"] or "(empty)") for m in MODELS}
            mapping[f"{qid}|{cond}"] = {v: k for k, v in m2l.items()}  # letter -> model
            packets.append({
                "packet_id": f"{qid}|{cond}",
                "qid": qid, "condition": cond, "query_type": q["query_type"],
                "question": q["question"],
                "gold_doc_ids": q.get("expected_doc_ids", []),
                "reference": reference(ret.get(qid, {}).get("results", [])),
                "answers": {L: answers[L] for L in LETTERS},
            })
    (BENCH / "packets.jsonl").write_text("".join(json.dumps(p) + "\n" for p in packets))
    (BENCH / "mapping.json").write_text(json.dumps(mapping, indent=2))
    print(f"wrote {len(packets)} judging packets ({len(packets)//2} questions x 2 conditions)")
    print("anonymization mapping -> eval/bench/mapping.json")

if __name__ == "__main__":
    main()
