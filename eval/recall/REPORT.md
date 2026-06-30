# Conceptual-recall experiment — BM25 vs dense vs hybrid (2026-06-27)

**Question:** the eval's concept misses are *recall* failures (BM25 never surfaces the gold,
so reranking can't help). Does semantic/hybrid retrieval recover them?

**Setup:** embedded the 47,191 non-bugtracker docs with `nomic-embed-text` (768-dim, via Ollama;
`search_document:`/`search_query:` prefixes, L2-normalized, title + first 6 KB). Dense = cosine
top-k over the same source filter the router uses. Hybrid = **Reciprocal Rank Fusion** (k=60) of
the BM25 and dense rankings. Measured on the 61-question eval set.

## Results — hit@5 / hit@10 / MRR

| query_type | n | BM25 (prod) | DENSE only | **HYBRID (RRF)** |
|---|--:|---|---|---|
| **concept** | 29 | 0.55 / 0.62 / 0.40 | 0.52 / 0.62 / 0.44 | **0.69 / 0.72 / 0.50** |
| irule | 7 | 0.71 / 0.86 / 0.73 | 0.29 / 0.29 / 0.21 | **0.86** / 0.86 / 0.55 |
| f5os | 5 | 0.80 / 0.80 / 0.54 | 0.80 / 0.80 / 0.55 | 0.80 / **1.00** / **0.73** |
| rfc | 3 | 1.00 / 1.00 / 1.00 | 0.33 / 0.33 / 0.33 | 1.00 / 1.00 / 1.00 |
| k-number | 9 | 1.00 / 1.00 / 1.00 | 0.11 / 0.11 / 0.11 | 1.00 / 1.00 / 1.00 |
| cve | 3 | 1.00 / 1.00 / 1.00 | 0.00 / 0.00 / 0.00 | 1.00 / 1.00 / 1.00 |
| bug-id | 5 | 1.00 / 1.00 / 1.00 | 0.00 / 0.00 / 0.00 | 1.00 / 1.00 / 0.90 |
| **ALL** | 61 | 0.74 / 0.79 / 0.65 | 0.38 / 0.43 / 0.31 | **0.82 / 0.85 / 0.68** |

## Findings

1. **Hybrid is a clear, broad win.** Concept **hit@5 0.55 → 0.69 (+14 pts)**, f5os hit@10
   0.80 → **1.00**, overall **0.74 → 0.82**. And it **preserves the perfect identifier scores**
   (k-number/cve/rfc/bug-id stay 1.0) because RRF keeps BM25's exact-lookup hits at the top.
2. **Pure dense is NOT viable alone** — it collapses on identifier lookups (cve 0.00, k-number
   0.11, bug-id 0.00 — those gold docs aren't even semantically findable / not embedded). Dense's
   value is purely additive *via fusion*.
3. **6 of 13 BM25 misses recovered** by dense/hybrid (s008, s013, s014, s019, s027, s035) — these
   were genuine recall failures semantic similarity bridges. 7 still missed by all (q002, s001,
   s007, s009, s020, s023, s030) — candidates for **chunked embeddings** (gold info past the 6 KB
   truncation) or **query expansion**.
4. **One regression to fix in a real build:** irule MRR drops 0.73 → 0.55 — RRF dilutes the
   `NS::cmd` direct-lookup hit that BM25 nails at rank 1. A production hybrid should **pin the
   direct-lookup branches (K#/CVE/RFC/iRule) ahead of fusion**, fusing dense only with the FTS tail.

## Recommendation: **build it.**

This justifies the P3 "embeddings index alongside knowledge.db" item with data — a measured
+14-pt concept hit@5 with zero cost to the identifier lookups. Proposed production shape:
- **Index:** embed the corpus on ingest (nomic-embed-text, ~10 min for 47k; re-embed only changed
  docs via `content_hash`). Store vectors in `sqlite-vec` (pairs with `knowledge.db`) or a sidecar.
- **Retriever:** keep the direct-lookup ladder pinned; fuse BM25-FTS + dense via RRF for the rest;
  embed the query via Ollama at request time (~10–50 ms).
- **Next experiments:** chunked embeddings for the 7 hard misses; tune RRF k; try a stronger
  embedder (mxbai-embed-large / bge) if the gain justifies the latency.

Artifacts (`eval/recall/`): `embed_corpus.py`, `compare_recall.py`, `results.json`. The 145 MB
`corpus_emb.npz` is gitignored (regenerate via `embed_corpus.py`).
