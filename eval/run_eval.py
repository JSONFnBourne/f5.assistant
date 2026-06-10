#!/usr/bin/env python3
"""RAG retrieval eval harness (new; lives under eval/ only).

Pipeline per question:
  1. Retrieve with the REAL /knowledge retriever via eval/retrieve.cjs (which
     requires the tsc-compiled webapp/lib searchDocuments + classifyQuery).
  2. Compute retrieval metrics vs expected_doc_ids: hit-rate@5, hit-rate@10, MRR.
  3. Generate an answer with the stock qwen2.5:7b using the SAME grounding prompt
     as webapp/app/api/knowledge/route.ts. Answer text is captured for human
     review only — NO auto-judge.
  4. Write eval/results/<timestamp>.json (per-question + aggregate metrics).

Touches nothing outside eval/. Rollback = rm -rf eval/.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import re
from datetime import datetime, timezone

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EVAL_DIR)
WEBAPP = os.path.join(REPO, "webapp")
# abspath: retrieve.cjs runs with cwd=webapp/, so a relative EVAL_QUESTIONS would not resolve.
QUESTIONS = os.path.abspath(os.environ.get("EVAL_QUESTIONS", os.path.join(EVAL_DIR, "questions.jsonl")))
RESULTS_DIR = os.path.join(EVAL_DIR, "results")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("EVAL_MODEL", "qwen2.5:7b")  # /knowledge stock model; override via EVAL_MODEL

# ── grounding prompt: faithful copy of webapp/app/api/knowledge/route.ts ──────
SHARED_RULES = """
Rules:
1. STRICT GROUNDING: Answer ONLY from the provided Context below. Do NOT use your general training data to supplement, expand, or invent information not present in the Context.
2. SCOPE: Answer ONLY what the user asked. Do not volunteer information about related topics, adjacent codes, other error classes, or additional categories that were not part of the question.
3. Cite sources (titles and URLs) only from the provided context.
4. All processing is LOCAL. Do not reference external internet sources.
5. Use Markdown code blocks only for real, verified syntax from the context — never write pseudo-code or invent configuration examples.
"""

MODE_INTRO = {
    "f5": """You are an expert F5 BIG-IP engineer and solutions architect.
Answer this question strictly in the context of F5 product configuration, behavior, and best practices.
Use only the F5 CloudDocs and iRules documentation context provided below.
Do NOT include generic protocol theory or RFC explanations unless they appear verbatim in the context.""",
    "rfc": """You are an expert network engineer specializing in open protocol standards.
Answer this question strictly in the context of RFC standards and protocol mechanics.
Use only the RFC documentation context provided below.
Do NOT include any vendor-specific content (F5, Cisco, Juniper, or any other vendor), product configuration examples, or pseudo-code.
If the context contains vendor material, ignore it entirely.""",
    "general": """You are an expert F5 BIG-IP engineer and network protocols specialist.
Draw from both F5 product documentation and RFC standards as relevant to exactly what was asked.
Use the documentation context provided below.
Do NOT include vendor-specific configuration examples unless they appear verbatim in the context.""",
}

K_RE = re.compile(r"\bk\d{4,}\b", re.IGNORECASE)


def build_context(docs: list[dict]) -> str:
    # route.ts:75-77
    return "\n\n---\n\n".join(
        f"[Source: {d.get('title')}] ({d.get('url')})\n{(d.get('content') or '')[:1000]}..."
        for d in docs
    )


def build_system_prompt(mode: str, context: str, question: str) -> str:
    prompt = f"{MODE_INTRO[mode]}\n{SHARED_RULES}\nContext:\n{context}"
    # route.ts:83-87 — K-article authority note
    cited = K_RE.findall(question)
    if cited:
        klist = ", ".join(k.upper() for k in cited)
        prompt = (
            f"AUTHORITY NOTE: The user explicitly referenced {klist}. If this document is present "
            f"in the Context below, base your answer primarily on its content and cite it as the "
            f"principal source. Do not draw from other context sources unless the cited document is "
            f"silent on the specific point asked.\n\n" + prompt
        )
    return prompt


# ── staleness guard ──────────────────────────────────────────────────────────
# eval/_gen/*.js are tsc-compiled copies of webapp/lib/*.ts. If a source .ts is
# newer than its compiled .js, the harness would silently eval a stale retriever.
_GEN_PAIRS = [
    (os.path.join(WEBAPP, "lib", "db.ts"), os.path.join(EVAL_DIR, "_gen", "db.js")),
    (
        os.path.join(WEBAPP, "lib", "knowledgeClassifier.ts"),
        os.path.join(EVAL_DIR, "_gen", "knowledgeClassifier.js"),
    ),
]

TSC_CMD = """cd webapp && node_modules/.bin/tsc lib/db.ts lib/knowledgeClassifier.ts \\
  --outDir ../eval/_gen --rootDir lib --module commonjs --target es2020 \\
  --esModuleInterop --skipLibCheck --moduleResolution node"""


def check_gen_freshness() -> None:
    stale = []
    for src, gen in _GEN_PAIRS:
        if not os.path.exists(gen):
            stale.append(f"  MISSING: {gen}")
        elif os.path.getmtime(src) > os.path.getmtime(gen):
            stale.append(f"  STALE:   {gen} (source {src} is newer)")
    if stale:
        sys.exit(
            "[eval/_gen is stale — the compiled retriever no longer matches webapp/lib]\n"
            + "\n".join(stale)
            + "\n\nRe-transpile from the repo root, then re-run:\n\n"
            + TSC_CMD
            + "\n"
        )


# ── retrieval (delegates to the real retriever) ──────────────────────────────
def retrieve_all() -> dict[str, dict]:
    env = dict(os.environ, NODE_PATH=os.path.join(WEBAPP, "node_modules"))
    proc = subprocess.run(
        ["node", os.path.join(EVAL_DIR, "retrieve.cjs"), QUESTIONS],
        cwd=WEBAPP, env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"[retrieve.cjs failed]\n{proc.stderr}")
    return {r["id"]: r for r in json.loads(proc.stdout)}


# ── generation (stock model, no judge) ───────────────────────────────────────
def call_ollama(system: str, user: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # mirror route.ts:99-104
        "options": {"num_ctx": 8192, "temperature": 0.3, "top_k": 40, "top_p": 0.9},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


# ── metrics ──────────────────────────────────────────────────────────────────
def metrics_for(ranked_ids: list[str], expected: list[str]) -> dict:
    exp = set(expected)
    rank = None
    for i, did in enumerate(ranked_ids, start=1):
        if did in exp:
            rank = i
            break
    return {
        "first_relevant_rank": rank,
        "hit_at_5": 1.0 if rank is not None and rank <= 5 else 0.0,
        "hit_at_10": 1.0 if rank is not None and rank <= 10 else 0.0,
        "reciprocal_rank": (1.0 / rank) if rank is not None else 0.0,
    }


def main() -> None:
    check_gen_freshness()
    questions = [json.loads(l) for l in open(QUESTIONS, encoding="utf-8") if l.strip()]
    retrieval = retrieve_all()

    per_q = []
    for q in questions:
        r = retrieval.get(q["id"], {"mode": "general", "results": []})
        mode = r["mode"]
        docs = r["results"]
        ranked_ids = [d["doc_id"] for d in docs]
        # Rows with no gold (e.g. harvested concept candidates pre-review) are UNSCORED:
        # we still retrieve + generate for qualitative review, but they don't count as misses.
        scored = bool(q.get("expected_doc_ids"))
        m = metrics_for(ranked_ids, q["expected_doc_ids"]) if scored else {
            "first_relevant_rank": None, "hit_at_5": None, "hit_at_10": None, "reciprocal_rank": None}

        # generation: mirror the route's production slice (5, or 8 for general)
        slice_n = 8 if mode == "general" else 5
        ctx_docs = docs[:slice_n]
        context = build_context(ctx_docs)
        system = build_system_prompt(mode, context, q["question"])

        answer, gen_err, tok_s = None, None, None
        try:
            resp = call_ollama(system, q["question"])
            answer = (resp.get("message") or {}).get("content")
            ec, ed = resp.get("eval_count"), resp.get("eval_duration")
            if ec and ed:
                tok_s = round(ec / (ed / 1e9), 1)
        except Exception as exc:  # noqa: BLE001
            gen_err = str(exc)[:200]

        per_q.append({
            "id": q["id"],
            "question": q["question"],
            "query_type": q.get("query_type"),
            "expected_doc_ids": q.get("expected_doc_ids", []),
            "mode": mode,
            "scored": scored,
            "retrieved_doc_ids_ranked": ranked_ids,
            **m,
            "answer_captured": answer is not None,
            "gen_tokens_per_sec": tok_s,
            "answer": answer,
            "generation_error": gen_err,
            "context_sources": [{"doc_id": d["doc_id"], "title": d["title"]} for d in ctx_docs],
        })

    n = len(per_q)
    scored_q = [p for p in per_q if p["scored"]]
    ns = len(scored_q)

    # Per-query_type breakout. Identifier-derived types (k-number, rfc, irule)
    # hit direct-lookup branches and are near-tautological — the blended headline
    # overstates retrieval quality, so report each type separately.
    by_type: dict[str, list[dict]] = {}
    for p in scored_q:
        by_type.setdefault(p["query_type"] or "unknown", []).append(p)
    per_type = {
        qt: {
            "n_scored": len(rows),
            "hit_rate_at_5": round(sum(r["hit_at_5"] for r in rows) / len(rows), 4),
            "hit_rate_at_10": round(sum(r["hit_at_10"] for r in rows) / len(rows), 4),
            "mrr": round(sum(r["reciprocal_rank"] for r in rows) / len(rows), 4),
        }
        for qt, rows in sorted(by_type.items())
    }

    agg = {
        "per_query_type": per_type,
        "n_questions": n,
        "n_scored": ns,
        "n_unscored": n - ns,
        "hit_rate_at_5": round(sum(p["hit_at_5"] for p in scored_q) / ns, 4) if ns else None,
        "hit_rate_at_10": round(sum(p["hit_at_10"] for p in scored_q) / ns, 4) if ns else None,
        "mrr": round(sum(p["reciprocal_rank"] for p in scored_q) / ns, 4) if ns else None,
        "answers_captured": sum(1 for p in per_q if p["answer_captured"]),
        "model": MODEL,
    }
    _toks = [p["gen_tokens_per_sec"] for p in per_q if p.get("gen_tokens_per_sec")]
    agg["mean_tokens_per_sec"] = round(sum(_toks) / len(_toks), 1) if _toks else None

    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(RESULTS_DIR, f"{ts}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": ts, "aggregate": agg, "questions": per_q}, fh, indent=2)

    print("===== PER-QUERY-TYPE RETRIEVAL METRICS (primary) =====")
    print(f"  {'query_type':<12} {'n':>3} {'hit@5':>7} {'hit@10':>7} {'mrr':>7}")
    for qt, m in per_type.items():
        print(f"  {qt:<12} {m['n_scored']:>3} {m['hit_rate_at_5']:>7.3f} "
              f"{m['hit_rate_at_10']:>7.3f} {m['mrr']:>7.3f}")
    print("  (identifier-derived types — k-number/rfc/irule — exercise direct-lookup")
    print("   branches and are near-tautological; weight the concept rows most.)")

    print("\n===== BLENDED AGGREGATE (inflated by identifier types) =====")
    print(json.dumps({k: v for k, v in agg.items() if k != "per_query_type"}, indent=2))
    print("\n===== PER-QUESTION (scored only) =====")
    for p in per_q:
        if not p["scored"]:
            continue
        print(f"  {p['id']} [{p['query_type']:9s}] mode={p['mode']:7s} "
              f"hit@5={p['hit_at_5']:.0f} hit@10={p['hit_at_10']:.0f} "
              f"rr={p['reciprocal_rank']:.3f} rank={p['first_relevant_rank']} "
              f"answer={'yes' if p['answer_captured'] else 'NO'}")
    if n - ns:
        print(f"  (+{n - ns} unscored rows — retrieved + answered for review, no gold)")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
