# eval/harvest — eval question CANDIDATE assembly

New tooling only. **No source edits, no commits. `knowledge.db` opened read-only.**
**No LLM-generated questions** anywhere in here.

Two classes of candidate are produced, then a human review pass gates assembly into
`eval/questions.jsonl`.

## 1. Identifier class — `sample_identifiers.py` (automated)
Samples real identifiers from `documents` (deterministic, `seed=42`) and renders each through a
**fixed lookup template** (no generation):
- 8 K-articles (f5_kb + f5_security mix) → `k-number`
- 3 CVEs (from `keywords`) → `cve`
- 3 iRule commands (source `irules`) → `irule`
- 2 RFCs → `rfc`

Gold (`expected_doc_ids`) is the sampled `doc_id` — **tautological by construction**. Output:
`identifier_candidates.jsonl` (harness schema, gold pre-filled).

## 2. Concept / troubleshooting class — `harvest_community.py` (harvested)
Pulls real question threads from `community.f5.com` (technicalforum sitemaps → thread pages;
`__NEXT_DATA__` gives `subject` + first-post `body` + tags). Respects robots.txt, throttled,
clear UA. Filters: drops announcements and <8-word first posts; near-dedups on title; stratifies
to ~50 across tags (LTM, DNS/GTM, F5OS, iRules, upgrades). Output: `concept_candidates.jsonl`
(raw, **no gold**) and the unified review file.

**HARD LINE:** concept candidates carry **EMPTY `expected_doc_ids`** — gold is never proposed here,
and the retriever is **never** run against candidates.

## Review file → `candidates_review.md`
One parseable block per candidate. The human:
- flips `- [ ] accept` to `- [x] accept` for keepers,
- edits `- question:` text as needed,
- fills `- expected_doc_ids:` (for concept rows; identifier rows are pre-filled).

Block format (kept stable so a later assembler can parse it):
```
### <id>  ·  tag: <tag>  ·  query_type: <qt>
- [ ] accept
- question: <text>
- expected_doc_ids: <comma-separated doc_ids, or empty>
- source_url: <url>
- first_post: <one-line excerpt, concept only>
- notes: <provenance>
```

## STOP / step 3 (runs only AFTER the human review pass)
Step 3 is **not** in this tooling yet. After review it will: validate gold refs against
`documents.doc_id` (exact, then `K000######` / bare-numeric normalization; report found/not-found),
assemble accepted rows into `eval/questions.jsonl`, and print a per-`query_type` balance report.

Rollback: `rm -rf eval/harvest/` (and the candidate files it wrote).
