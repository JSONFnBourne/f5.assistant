# F5 Assistant — 14B Model Bake-off (2026-06-27)

**Setup:** 4 models × 2 conditions (RAG + closed-book) × 50 F5 questions = 400 answers.
All **Q4_K_M** (apples-to-apples), served via Ollama 0.30.11, identical retrieved context +
grounding prompt per question. Judged **blind** (answers anonymized A/B/C/D) by Claude Opus 4.8
across 10 parallel judges, scoring correctness · grounding · completeness · conciseness (0–5 each, /20).

## Leaderboard (mean /20 over 100 answers each)

| Rank | Model | **Total** | correct | ground | complete | concise | wins | cost/answer |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 🥇 | **qwen3:14b** | **14.73** | 3.56 | 3.45 | 3.80 | 3.92 | 44 | 12.8 s · 843 tok |
| 🥈 | **qwen2.5:14b** *(incumbent)* | **14.48** | 3.52 | 3.46 | 3.52 | 3.98 | 35 | **4.4 s · 267 tok** |
| 🥉 | phi4 | 13.69 | 3.25 | 3.29 | 3.43 | 3.72 | 21 | 5.2 s · 306 tok |
| ❌ | phi4-reasoning | 8.51 | 2.78 | 2.49 | 3.06 | 0.18 | 0 | 46 s · 2524 tok |

**qwen3 wins on points; qwen2.5 wins on value.** The gap is 0.25/20 (~1.7%) — but qwen3 pays
**~3× the latency** for it.

## Where each wins

**By condition** (deployment is RAG):
| | RAG | closed-book |
|---|---:|---:|
| qwen3 | **15.94** 🥇 | 13.52 |
| qwen2.5 | 15.12 | **13.84** 🥇 |
| phi4 | 15.58 | 11.80 |
| phi4-reasoning | 9.24 | 7.78 |

- **With context (RAG):** qwen3 > phi4 > qwen2.5. Thinking helps synthesis.
- **Closed-book (innate knowledge):** qwen2.5 > qwen3 > phi4. The incumbent has the best F5
  knowledge baked in and is the most *honest* about gaps. phi4 falls apart without context.

**By domain** (mean /20):
| | concept | iRule | F5OS | k-number | bug-id | cve | rfc |
|---|---:|---:|---:|---:|---:|---:|---:|
| qwen2.5 | 14.81 | 12.21 | 12.2 | **16** | **18** | 15.5 | 15 |
| qwen3 | **14.98** | **15.43** | **12.5** | 12.5 | 15.7 | 15.8 | **15.5** |
| phi4 | 13.53 | 11.93 | 12.4 | **16** | 16.7 | **17.5** | 13.5 |

- **qwen3's entire edge is iRules** (+3.2 over qwen2.5) and a hair on concept.
- **qwen2.5 is best at identifier-grounding** (k-number 16, bug-id 18) — it faithfully
  summarizes the retrieved doc instead of over-thinking it. qwen3 is *worse* on direct lookups
  (k-number 12.5) — its reasoning over-complicates "just summarize this article."
- **F5OS is everyone's weak spot** (~12) — the thinnest area of the knowledge base.

## Why

- **qwen3:14b** — wins by *thinking* (≈2,300 reasoning chars/answer, cleanly separated by
  Ollama into a `thinking` field). Buys completeness + iRule synthesis. Costs: 3× latency,
  and the thinking actively *hurts* on simple grounding/lookup tasks.
- **qwen2.5:14b** — concise, faithful, fewest **wrong** answers (5 vs qwen3's 10, phi4's 14),
  best innate knowledge. The value champion: ~90% of qwen3's quality at **1/3 the latency**.
- **phi4** — fast and good *with* context, but the most hallucination-prone of the usable three
  (22/100) and collapses closed-book. No reason to switch to it from qwen2.5.
- **phi4-reasoning** — disqualified on three counts: (1) Ollama doesn't parse its reasoning
  format, so it **dumps raw chain-of-thought as the answer** (100/100 answers, ~12k chars,
  conciseness 0.18) — unusable for a clean assistant UI without custom parsing; (2) **10× the
  cost** (46 s/answer); (3) even underneath the noise, its correctness/grounding are lowest and
  it hallucinates most (30/100).

**Hallucination flags (/100):** phi4-reasoning 30 · phi4 22 · qwen2.5 16 · qwen3 15.

## Recommendation

- **Keep `qwen2.5:14b` as the default.** For an *interactive* assistant, a 13-second qwen3 wait
  for a +1.7% quality bump is a bad trade — and qwen2.5 is actually *better* on the grounded
  identifier-lookup queries (K-articles, bug IDs) that the new sources emphasize.
- **Consider `qwen3:14b` only if iRule generation/explanation is a priority** (its standout
  strength) and ~13 s latency is acceptable. A future option: route iRule-class queries to qwen3,
  everything else to qwen2.5.
- **Drop phi4-reasoning** for this use case. **phi4** is a viable fast alternative but not an
  upgrade over the incumbent.

## Caveats
- All Q4_K_M. A **victory lap** at Q5_K_M / Q6_K_M on the top two (qwen2.5 vs qwen3) could shift
  the razor-thin margin — cheap to run now that we know who's worth it.
- phi4-reasoning's score reflects an Ollama integration failure (no reasoning separation), not
  purely model quality — but that failure *is* the deployment reality out of the box.
- Single Claude judge per answer (blind, anonymized). Artifacts: `eval/bench/{answers,packets,
  scores/,mapping.json,aggregate.json}` — fully reproducible.

---

## Quant victory lap — qwen2.5:14b at Q4 vs Q5 vs Q6 (2026-06-27)

Same 50 questions × 2 conditions, fresh **blind 3-way** judging (A/B/C = the three quants;
judges told the model is identical so differences are subtle). n=100 answers/quant.

| Quant | **Total/20** | correct | ground | complete | concise | wins | cost | VRAM |
|---|---:|---:|---:|---:|---:|---:|---|---|
| Q4_K_M *(current)* | 14.94 | 3.70 | 3.66 | 3.78 | 3.80 | 35 | 4.4 s · 267 tok | 9.0 GB |
| **Q5_K_M** | **15.23** | 3.76 | 3.75 | 3.77 | 3.95 | **40** | 5.1 s · 267 tok | 10 GB |
| Q6_K | 15.15 | 3.71 | 3.74 | 3.63 | 4.07 | 25 | 5.4 s · 244 tok | 12 GB |

- **Q5_K_M is the sweet spot:** best total, most wins (40), best RAG (15.78). The bump over Q4 is
  **+0.29/20 (~2%)** — small but consistent (it also wins RAG head-to-head 23 vs 14).
- **Q6_K is NOT worth it:** no better than Q5 (15.15 vs 15.23), fewest wins, +3 GB VRAM. Diminishing
  returns — the extra precision doesn't buy quality here.
- **Where Q5/Q6 help:** k-number grounding (13.3 vs Q4's **11.7**) — higher precision = more faithful
  verbatim summary of retrieved K-articles — and a hair on concept. iRule/bug-id/F5OS are flat.
- The spread is near judge-noise; treat this as "Q5 is a free, low-risk bump," not a leap.

**Recommendation: bump the live assistant Q4 → `qwen2.5:14b-instruct-q5_K_M`.** It fits comfortably
on the 16 GB card (10 GB), adds ~0.7 s/answer, and gives a modest, consistent quality gain
concentrated in conceptual + K-article grounding. **Skip Q6_K** — pure cost, no gain.

