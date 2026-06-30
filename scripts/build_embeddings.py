#!/usr/bin/env python3
"""Build the dense-retrieval index the webapp's hybrid retriever loads:
  db/knowledge_vec.f32   raw float32, N x 768, row-major, L2-normalized
  db/knowledge_vec.json  {model, dim, count, doc_ids[], sources[]}  (parallel to rows)

Embeds the non-bugtracker corpus with nomic-embed-text via Ollama (bug-id queries
are served by BM25 direct-lookup, so bugtracker is not embedded). GPU via Ollama.

  pipeline/.venv/bin/python scripts/build_embeddings.py            # full rebuild
  pipeline/.venv/bin/python scripts/build_embeddings.py --from-npz eval/recall/corpus_emb.npz
"""
from __future__ import annotations
import argparse, json, sqlite3, time, urllib.request
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "db" / "knowledge.db"
OUT_F32 = REPO / "db" / "knowledge_vec.f32"
OUT_JSON = REPO / "db" / "knowledge_vec.json"
OLLAMA = "http://127.0.0.1:11434/api/embed"
MODEL = "nomic-embed-text"
DIM = 768
BATCH = 64
MAX_CHARS = 6000

def _embed(texts: list[str]) -> np.ndarray:
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return np.array(json.load(r)["embeddings"], dtype=np.float32)

def _corpus():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return c.execute(
        "SELECT doc_id, source, coalesce(title,''), coalesce(content,'') "
        "FROM documents WHERE source != 'bugtracker' ORDER BY id"
    ).fetchall()

def _write(vecs: np.ndarray, doc_ids: list[str], sources: list[str]):
    assert vecs.dtype == np.float32 and vecs.shape[1] == DIM
    OUT_F32.write_bytes(np.ascontiguousarray(vecs).tobytes())
    OUT_JSON.write_text(json.dumps(
        {"model": MODEL, "dim": DIM, "count": len(doc_ids), "doc_ids": doc_ids, "sources": sources}
    ))
    print(f"wrote {OUT_F32.name} ({vecs.nbytes/1e6:.0f} MB) + {OUT_JSON.name} ({len(doc_ids)} docs)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-npz", type=Path, help="convert an existing eval/recall/corpus_emb.npz")
    args = ap.parse_args()

    if args.from_npz:
        d = np.load(args.from_npz, allow_pickle=True)
        _write(d["vecs"].astype(np.float32), [str(x) for x in d["doc_ids"]], [str(x) for x in d["sources"]])
        return

    rows = _corpus()
    print(f"embedding {len(rows)} docs with {MODEL}…", flush=True)
    vecs = np.empty((len(rows), DIM), dtype=np.float32)
    t0 = time.time()
    for i in range(0, len(rows), BATCH):
        b = rows[i : i + BATCH]
        e = _embed([f"search_document: {ti}\n{co[:MAX_CHARS]}" for (_, _, ti, co) in b])
        e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
        vecs[i : i + len(b)] = e
        if (i // BATCH) % 100 == 0:
            print(f"  {i+len(b)}/{len(rows)}", flush=True)
    _write(vecs, [r[0] for r in rows], [r[1] for r in rows])
    print(f"done in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
