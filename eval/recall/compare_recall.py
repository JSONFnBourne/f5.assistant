#!/usr/bin/env python3
"""Conceptual-recall experiment: compare BM25 (production) vs dense (nomic-embed)
vs hybrid (Reciprocal Rank Fusion) on the eval set, focused on whether semantic
retrieval recovers the concept misses BM25 can't surface.

Needs corpus_emb.npz (embed_corpus.py) + bm25.json (retrieve.cjs). Embeds the 61
queries via Ollama (tiny). Run from repo root:
  pipeline/.venv/bin/python eval/recall/compare_recall.py
"""
from __future__ import annotations
import json, urllib.request
from collections import defaultdict
from pathlib import Path
import numpy as np

REC = Path(__file__).resolve().parent
REPO = REC.parent.parent
OLLAMA = "http://127.0.0.1:11434/api/embed"

# f5-mode sources (no bugtracker — concept queries exclude it; it isn't embedded anyway)
F5_SOURCES = {"irules", "clouddocs", "f5_kb", "f5_security", "xc_techdocs", "techdocs", "community", "f5os_api"}

def embed_query(q: str) -> np.ndarray:
    body = json.dumps({"model": "nomic-embed-text", "input": [f"search_query: {q}"]}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        v = np.array(json.load(r)["embeddings"][0], dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)

def metrics(ranked: list[str], gold: set[str]) -> tuple[float, float, float]:
    rank = next((i for i, d in enumerate(ranked, 1) if d in gold), None)
    return (1.0 if rank and rank <= 5 else 0.0,
            1.0 if rank and rank <= 10 else 0.0,
            1.0 / rank if rank else 0.0)

def rrf(rank_lists: list[list[str]], k: int = 60) -> list[str]:
    score: dict[str, float] = defaultdict(float)
    for rl in rank_lists:
        for r, did in enumerate(rl, 1):
            score[did] += 1.0 / (k + r)
    return [d for d, _ in sorted(score.items(), key=lambda x: -x[1])]

def main():
    d = np.load(REC / "corpus_emb.npz", allow_pickle=True)
    vecs, doc_ids, sources = d["vecs"], d["doc_ids"], d["sources"]
    f5_mask = np.array([s in F5_SOURCES for s in sources])
    rfc_mask = sources == "rfc"

    def dense_topk(qv, mode, k=10):
        mask = f5_mask if mode == "f5" else rfc_mask if mode == "rfc" else np.ones(len(sources), bool)
        idx = np.where(mask)[0]
        sims = vecs[idx] @ qv
        top = idx[np.argsort(-sims)[:k]]
        return [doc_ids[i] for i in top]

    bm25 = {r["id"]: (r["mode"], [x["doc_id"] for x in r["results"]]) for r in json.load(open(REC / "bm25.json"))}
    qs = [json.loads(l) for l in open(REPO / "eval" / "questions.jsonl") if l.strip()]

    by = defaultdict(lambda: defaultdict(list))   # qtype -> method -> [(h5,h10,mrr)]
    misses_recovered = []
    perq = []
    for q in qs:
        gold = set(q["expected_doc_ids"]); qt = q["query_type"]
        mode, bm = bm25[q["id"]]
        qv = embed_query(q["question"])
        dense = dense_topk(qv, mode, 10)
        hybrid = rrf([bm, dense])[:10]
        m = {"bm25": metrics(bm, gold), "dense": metrics(dense, gold), "hybrid": metrics(hybrid, gold)}
        for method, mm in m.items():
            by[qt][method].append(mm)
            by["ALL"][method].append(mm)
        # did a BM25 miss (gold not in top-10) get recovered by dense/hybrid?
        bm_hit10 = m["bm25"][1]
        if bm_hit10 == 0.0 and gold:
            rec = [meth for meth in ("dense", "hybrid") if m[meth][1] == 1.0]
            misses_recovered.append((q["id"], qt, rec))
        perq.append((q["id"], qt, m))

    def agg(rows):
        n = len(rows)
        return (sum(r[0] for r in rows) / n, sum(r[1] for r in rows) / n, sum(r[2] for r in rows) / n)

    print("=== BM25 vs DENSE vs HYBRID  (hit@5 / hit@10 / MRR) ===")
    order = ["concept", "irule", "f5os", "rfc", "k-number", "cve", "bug-id", "ALL"]
    for qt in order:
        if qt not in by: continue
        n = len(by[qt]["bm25"])
        cells = []
        for meth in ("bm25", "dense", "hybrid"):
            a = agg(by[qt][meth]); cells.append(f"{a[0]:.2f}/{a[1]:.2f}/{a[2]:.2f}")
        print(f"  {qt:<9} n={n:<3}  BM25 {cells[0]:<16} DENSE {cells[1]:<16} HYBRID {cells[2]}")

    print(f"\n=== BM25 misses (gold not in top-10): {len(misses_recovered)} ===")
    for qid, qt, rec in misses_recovered:
        tag = ("RECOVERED by " + "+".join(rec)) if rec else "still missed by all"
        print(f"  {qid} [{qt:8}] {tag}")

    json.dump({"by_type": {qt: {m: agg(by[qt][m]) for m in ("bm25","dense","hybrid")} for qt in by},
               "misses": [(q, t, r) for q, t, r in misses_recovered]},
              open(REC / "results.json", "w"), indent=2)
    print(f"\nwrote {REC/'results.json'}")

if __name__ == "__main__":
    main()
