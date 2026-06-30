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

---

## Follow-up: chunked embeddings (2026-06-30)

The flat index embedded only `title + first 6 KB` of each doc, so 7 concept golds whose
answer sits deeper went unrecovered. Re-built the index **chunked** — each non-bugtracker
doc split into ≤8 overlapping 3 KB windows (`size=3000, overlap=300, cap=8`), 47,191 docs →
**143,547 chunks** (441 MB). `denseSearch` max-pools chunk scores back to the parent `doc_id`,
so the retriever contract (returns doc_ids) is unchanged. Same-harness A/B (`eval/retrieve.cjs`,
weighted RRF, dense weight 0.5) on the 61-question set:

| query_type | n | FLAT 6 KB (h5/h10/mrr) | **CHUNKED (h5/h10/mrr)** | Δh10 |
|---|--:|---|---|--:|
| concept | 29 | 0.62 / 0.72 / 0.43 | **0.69 / 0.83 / 0.45** | +0.10 |
| irule | 7 | 0.86 / 1.00 / 0.77 | 0.86 / 1.00 / 0.77 | — |
| f5os | 5 | 0.80 / 1.00 / 0.57 | 0.80 / 1.00 / 0.57 | — |
| rfc/k-number/cve/bug-id | 20 | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | — |
| **ALL** | 61 | 0.79 / 0.87 / 0.67 | **0.82 / 0.92 / 0.68** | +0.05 |

**Net: concept hit@10 0.72 → 0.83, ALL hit@10 0.87 → 0.92, zero regressions, all identifier
classes preserved at 1.0.** Recovered 3 concept rows at hit@10 (s007, s014, s020); none lost.
Still missed (q002, s001, s009, s023, s030) — **not** truncation: q002's golds are <1 KB docs
(semantic mismatch), the rest need query expansion or a stronger embedder, not deeper chunking.

Index build is now **content-hash incremental** (`scripts/build_embeddings.py` — reuses an
unchanged doc's chunk vectors, re-embeds only new/changed docs: full ~20 min, no-op refresh
~1.7 s) and the embedded-source ingesters call it after a run so the index stays fresh.
Result: `eval/results/20260630T085800Z-retrieval-chunked.json`.

### What did NOT help the remaining 5 misses (2026-06-30)

Chased q002/s001/s009/s023/s030 three ways; **none ships.** Diagnosis first: dense-only
already ranks s023's gold #4 and s030's #7 — they're **fusion-buried**, not unfound; q002's
gold are <1 KB tangential docs (the retrieved K8246/idle-timeout-overview are arguably better
answers — likely **bad gold**); s001 is a genuine semantic gap; only s009 responds to keyword
expansion.

1. **RRF dense-weight re-sweep on the chunked index** (0.5→1.6). Raising the weight to 1.0
   recovers s023 (#8) and s030 (#10) and lifts concept hit@10 0.83→0.86, but **costs** concept
   hit@5 0.69→0.66, irule hit@10 1.00→0.86, f5os hit@5 0.80→0.60; ≥1.3 craters identifiers
   (rfc/bug-id MRR collapse). **No clean win → keep dense weight 0.5.**
2. **HyDE / keyword query expansion** (14b generates a hypothetical answer / keyword set,
   embed that). Mixed and inconsistent — HyDE helped s030 (dense #7→#3) but hurt s009; keyword
   expansion helped s009 (#19→#10) but not others; q002/s001 unmoved. Plus a per-query LLM call
   (~1-3 s latency) on every search. **Not worth the latency for ~1 row → not shipped.**
3. **Stronger embedder: mxbai-embed-large (1024-d) vs nomic (768-d), single-variable dense-only
   A/B** (same chunking/corpus/max-pool). **Lateral, not better:** mxbai wins irule (dense
   0.43→1.00 hit@10) and f5os_api (0.80→1.00) and the buried misses (s023 #4→#2, s030 #7→#5),
   but **regresses concept hit@10 0.90→0.79 and f5os 0.80→0.60** — and concept hit@10 is exactly
   what nomic's dense feeds the hybrid. ALL dense hit@10 0.59→0.61 (wash). For a 1024-d cost
   (+33% query compute/storage) and a full corpus re-embed, the trade isn't justified.
   **Keep nomic.** (bge not built: mxbai is the stronger of the two on MTEB retrieval and came
   out lateral.)

**Genuinely open** (logged in TODO): q002 gold review (likely replace), s001 semantic gap, and
a *rank-gated* dense floor for pure-dense top-3 finds (s023/s030) that wouldn't globally
up-weight dense — deferred as risky tuning against a well-balanced aggregate.
