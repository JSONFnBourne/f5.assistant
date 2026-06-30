#!/usr/bin/env python3
"""Model bake-off harness: 4 models x 2 conditions (RAG, closed-book) x 50 F5 Qs.

Identical inputs per question across all models (fixed retrieved context for RAG,
identical prompt for closed-book) — only the generator varies. Models run
serialized; each is explicitly unloaded before the next loads so two 14B models
never collide on the 16 GB card. Resumable: skips (model,condition,qid) already
in answers.jsonl. Captures answer, reasoning trace (if any), and timing.

GPU-heavy. Run from repo root after the models are pulled:
  .venv/bin/python eval/bench/run_bench.py
"""
from __future__ import annotations
import json, os, re, time, urllib.request
from pathlib import Path

BENCH = Path(__file__).resolve().parent
REPO = BENCH.parent.parent
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
# overridable for focused runs (e.g. the quant victory lap) without duplicating logic
MODELS = os.environ.get("BENCH_MODELS", "qwen2.5:14b,qwen3:14b,phi4,phi4-reasoning").split(",")
ANSWERS = BENCH / os.environ.get("BENCH_ANSWERS", "answers.jsonl")
# reasoning models need room for the thinking trace + the answer; non-reasoning
# models emit EOS and stop early, so a high cap only bounds runaways.
NUM_PREDICT = {"qwen3:14b": 4096, "phi4-reasoning": 6144}
DEFAULT_NUM_PREDICT = 1536
THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)

# ── prompts: RAG half is a faithful copy of run_eval.py / route.ts ───────────
SHARED_RULES = """
Rules:
1. STRICT GROUNDING: Answer ONLY from the provided Context below. Do NOT use your general training data to supplement, expand, or invent information not present in the Context.
2. SCOPE: Answer ONLY what the user asked.
3. Cite sources (titles and URLs) only from the provided context.
4. All processing is LOCAL. Do not reference external internet sources.
5. Use Markdown code blocks only for real, verified syntax from the context — never invent configuration examples.
"""
MODE_INTRO = {
    "f5": "You are an expert F5 BIG-IP engineer and solutions architect.\nAnswer strictly in the context of F5 product configuration, behavior, and best practices.\nUse only the documentation context provided below.",
    "rfc": "You are an expert network engineer specializing in open protocol standards.\nAnswer strictly in the context of RFC standards. Use only the RFC context provided below.",
    "general": "You are an expert F5 BIG-IP engineer and network protocols specialist.\nUse the documentation context provided below.",
}
# closed-book: same persona, NO context, explicit honesty guard
CB_INTRO = {
    "f5": "You are an expert F5 BIG-IP engineer and solutions architect. Answer the question accurately and specifically from your own knowledge.",
    "rfc": "You are an expert network engineer specializing in open protocol standards. Answer accurately from your own knowledge.",
    "general": "You are an expert F5 BIG-IP engineer and network protocols specialist. Answer accurately from your own knowledge.",
}
CB_RULES = "\nIf you do not actually know the specific content of a referenced item (a K-number, bug ID, or RFC), say so plainly rather than inventing details. Be concise and concrete."

def build_context(docs, n):
    return "\n\n---\n\n".join(
        f"[Source: {d.get('title')}] ({d.get('url')})\n{(d.get('content') or '')[:1000]}..." for d in docs[:n]
    )

def rag_system(mode, docs):
    n = 8 if mode == "general" else 5
    return f"{MODE_INTRO[mode]}\n{SHARED_RULES}\nContext:\n{build_context(docs, n)}"

def cb_system(mode):
    return CB_INTRO[mode] + CB_RULES

def chat(model, system, user):
    body = json.dumps({
        "model": model, "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"num_ctx": 8192, "temperature": 0.3, "top_k": 40, "top_p": 0.9,
                    "num_predict": NUM_PREDICT.get(model, DEFAULT_NUM_PREDICT)},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.load(r)
    wall = time.time() - t0
    msg = resp.get("message") or {}
    content = msg.get("content") or ""
    thinking = msg.get("thinking") or ""
    if not thinking:  # fallback: some models embed <think>…</think> inline
        m = THINK_RE.findall(content)
        if m:
            thinking = "\n".join(m)
            content = THINK_RE.sub("", content).strip()
    ec, ed = resp.get("eval_count"), resp.get("eval_duration")
    return {
        "answer": content.strip(), "thinking": thinking.strip(),
        "gen_tokens": ec, "tok_s": round(ec / (ed / 1e9), 1) if ec and ed else None,
        "wall_s": round(wall, 1),
    }

def unload(model):
    try:
        body = json.dumps({"model": model, "keep_alive": 0}).encode()
        urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/generate", data=body,
                               headers={"Content-Type": "application/json"}), timeout=60).read()
    except Exception:
        pass

def main():
    qs = [json.loads(l) for l in open(BENCH / "benchmark.jsonl") if l.strip()]
    ret = {r["id"]: r for r in json.load(open(BENCH / "retrieval.json"))}
    done = set()
    if ANSWERS.exists():
        for l in open(ANSWERS):
            if l.strip():
                r = json.loads(l); done.add((r["model"], r["condition"], r["qid"]))
    out = open(ANSWERS, "a")
    for model in MODELS:
        print(f"\n===== {model} =====", flush=True)
        for cond in ("rag", "cb"):
            for q in qs:
                key = (model, cond, q["id"])
                if key in done:
                    continue
                r = ret.get(q["id"], {"mode": "f5", "results": []})
                mode = r["mode"]
                system = rag_system(mode, r["results"]) if cond == "rag" else cb_system(mode)
                try:
                    res = chat(model, system, q["question"])
                    err = None
                except Exception as e:
                    res = {"answer": "", "thinking": "", "gen_tokens": None, "tok_s": None, "wall_s": None}
                    err = str(e)[:200]
                rec = {"model": model, "condition": cond, "qid": q["id"],
                       "query_type": q["query_type"], "question": q["question"], **res, "error": err}
                out.write(json.dumps(rec) + "\n"); out.flush()
                print(f"  [{cond}] {q['id']:6s} {q['query_type']:8s} "
                      f"{res['wall_s']}s {res['gen_tokens']}tok {'ERR' if err else ''}", flush=True)
        unload(model)
        print(f"  unloaded {model}", flush=True)
    out.close()
    print("\nDONE — answers in", ANSWERS)

if __name__ == "__main__":
    main()
