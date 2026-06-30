#!/usr/bin/env python3
"""Build BLIND 3-way judging packets for the qwen2.5:14b quant victory lap:
Q4_K_M (from the main bake-off answers.jsonl) vs Q5_K_M vs Q6_K (answers_quant.jsonl).
Same fixed question + reference; only the quantization varies. Anonymized A/B/C.
GPU-free. Run after the quant generations finish.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path

BENCH = Path(__file__).resolve().parent
MODELS = ["qwen2.5:14b", "qwen2.5:14b-instruct-q5_K_M", "qwen2.5:14b-instruct-q6_K"]
LABELS = {"qwen2.5:14b": "Q4_K_M", "qwen2.5:14b-instruct-q5_K_M": "Q5_K_M", "qwen2.5:14b-instruct-q6_K": "Q6_K"}
LETTERS = ["A", "B", "C"]

def perm(seed):
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    ms = MODELS[:]; order = []
    while ms:
        order.append(ms.pop(h % len(ms))); h //= max(len(ms), 1) + 1
    return {m: LETTERS[i] for i, m in enumerate(order)}

def reference(docs, n=6):
    return "\n".join(f"- [{d.get('title')}] {(d.get('content') or '')[:350].strip()}" for d in docs[:n])

def main():
    ans = {}
    for fn in ("answers.jsonl", "answers_quant.jsonl"):
        p = BENCH / fn
        if p.exists():
            for l in open(p):
                if l.strip():
                    a = json.loads(l); ans[(a["model"], a["condition"], a["qid"])] = a
    qs = {q["id"]: q for q in (json.loads(l) for l in open(BENCH / "benchmark.jsonl") if l.strip())}
    ret = {r["id"]: r for r in json.load(open(BENCH / "retrieval.json"))}
    packets, mapping = [], {}
    for cond in ("rag", "cb"):
        for qid, q in qs.items():
            recs = {m: ans.get((m, cond, qid)) for m in MODELS}
            if any(r is None for r in recs.values()):
                continue
            m2l = perm(f"{qid}|{cond}|quant")
            mapping[f"{qid}|{cond}"] = {v: k for k, v in m2l.items()}
            packets.append({
                "packet_id": f"{qid}|{cond}", "qid": qid, "condition": cond,
                "query_type": q["query_type"], "question": q["question"],
                "reference": reference(ret.get(qid, {}).get("results", [])),
                "answers": {m2l[m]: (recs[m]["answer"] or "(empty)") for m in MODELS},
            })
    (BENCH / "packets_quant.jsonl").write_text("".join(json.dumps(p) + "\n" for p in packets))
    (BENCH / "mapping_quant.json").write_text(json.dumps(mapping, indent=2))
    print(f"wrote {len(packets)} quant packets; labels: {LABELS}")

if __name__ == "__main__":
    main()
