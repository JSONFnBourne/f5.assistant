#!/usr/bin/env python3
"""Embed the non-bugtracker knowledge corpus with nomic-embed-text (via Ollama)
for the conceptual-recall experiment. Saves L2-normalized vectors + parallel
doc_id/source arrays so dense retrieval is a single matrix multiply.

GPU (Ollama serves the embedder). Run from repo root:
  pipeline/.venv/bin/python eval/recall/embed_corpus.py
"""
from __future__ import annotations
import json, sqlite3, time, urllib.request
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parent.parent.parent
DB = REPO / "db" / "knowledge.db"
OUT = REPO / "eval" / "recall" / "corpus_emb.npz"
OLLAMA = "http://127.0.0.1:11434/api/embed"
MODEL = "nomic-embed-text"
BATCH = 64
MAX_CHARS = 6000  # ~1500 tokens; fits nomic's 2048 ctx with the title + prefix

def embed(texts: list[str]) -> np.ndarray:
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return np.array(json.load(r)["embeddings"], dtype=np.float32)

def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = c.execute(
        "SELECT doc_id, source, coalesce(title,''), coalesce(content,'') "
        "FROM documents WHERE source != 'bugtracker' ORDER BY id"
    ).fetchall()
    print(f"embedding {len(rows)} docs with {MODEL}…", flush=True)

    doc_ids = np.array([r[0] for r in rows], dtype=object)
    sources = np.array([r[1] for r in rows], dtype=object)
    vecs = np.empty((len(rows), 768), dtype=np.float32)

    t0 = time.time()
    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        texts = [f"search_document: {ti}\n{co[:MAX_CHARS]}" for (_, _, ti, co) in batch]
        e = embed(texts)
        # L2-normalize so cosine similarity == dot product
        e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
        vecs[i : i + len(batch)] = e
        if (i // BATCH) % 50 == 0:
            done = i + len(batch)
            rate = done / (time.time() - t0 + 1e-9)
            print(f"  {done}/{len(rows)}  ({rate:.0f} docs/s)", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez(OUT, vecs=vecs, doc_ids=doc_ids, sources=sources)
    print(f"saved {OUT}  ({vecs.shape}, {vecs.nbytes/1e6:.0f} MB) in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
