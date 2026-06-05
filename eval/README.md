# RAG eval harness (`eval/`)

New, self-contained. **Touches nothing outside `eval/`. Rollback = `rm -rf eval/`.**

Measures the `/knowledge` retrieval path against a hand-labelled gold set. It
**reuses the real retriever** — `eval/retrieve.cjs` requires the `tsc`-compiled
`webapp/lib/db.ts` (`searchDocuments`) and `webapp/lib/knowledgeClassifier.ts`
(`classifyQuery`); no retrieval logic is reimplemented here. The generation step
calls the same stock model (`qwen2.5:7b`) with the same grounding prompt as
`webapp/app/api/knowledge/route.ts`, and **captures the answer text only — there
is no auto-judge** (the 3B judge is untrusted).

## `questions.jsonl` schema (one JSON object per line)

| field | type | meaning |
|---|---|---|
| `id` | string | stable id, e.g. `q001` |
| `question` | string | the user query, fed verbatim to the retriever + model |
| `expected_doc_ids` | string[] | `documents.doc_id` values that SHOULD be retrieved to answer correctly (the gold set). For F5 KB/security these are K-numbers (`K000092981`); for RFCs `rfcNNNN`; for iRules/clouddocs the page URL; for techdocs `techdocs:...` |
| `query_type` | enum | `k-number` \| `concept` \| `irule` \| `rfc` \| `f5os` — exercises a distinct branch of the retrieval ladder |
| `notes` | string | free-text rationale (why these docs are relevant) |

`expected_doc_ids` are real `doc_id`s pulled from the live DB. Expand this file;
the 5 shipped rows are a smoke set, one per `query_type`.

## Metrics

Per question the harness records the ranked `doc_id` list from `searchDocuments`
(requested top-10), then computes against `expected_doc_ids`:

- **hit-rate@5** — 1 if any expected id appears in the top 5, else 0
- **hit-rate@10** — same over top 10
- **MRR** — reciprocal rank of the first relevant id (0 if none in top 10)

Aggregates are the mean across questions. Generation answers are stored for
qualitative human review, not scored.

> Note: the harness requests **top-10** to compute @5/@10/MRR; production
> `/knowledge` requests 5 (8 for `general` mode). The generation step mirrors the
> route's production slice (5, or 8 for general). `MODE_SOURCES` in
> `retrieve.cjs` and the system prompt in `run_eval.py` are faithful copies of
> `route.ts` and must be kept in sync if the route changes.

## Run

```bash
# 1) transpile the real retriever into eval/_gen/ (reads webapp/lib/*.ts, read-only)
cd webapp && node_modules/.bin/tsc lib/db.ts lib/knowledgeClassifier.ts \
  --outDir ../eval/_gen --rootDir lib --module commonjs --target es2020 \
  --esModuleInterop --skipLibCheck --moduleResolution node

# 2) run (needs ollama up for the generation step)
cd .. && .venv/bin/python eval/run_eval.py
```

Output: `eval/results/<timestamp>.json` (per-question + aggregate retrieval metrics).
