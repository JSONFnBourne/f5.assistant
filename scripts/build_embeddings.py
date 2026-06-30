#!/usr/bin/env python3
"""Build the dense-retrieval index the webapp's hybrid retriever loads:
  db/knowledge_vec.f32   raw float32, N_chunks x 768, row-major, L2-normalized
  db/knowledge_vec.json  {model, dim, chunked, chunk_params, n_chunks, docs[]}

Each non-bugtracker doc is split into overlapping CHUNKS (long K-articles carry
the answer well past the old 6 KB single-vector truncation — chunking embeds the
deep content so dense retrieval can surface it). Chunks are laid out doc-by-doc
contiguously; the webapp collapses chunk hits back to their parent doc_id
(max-pool) at query time, so the retriever contract (returns doc_ids) is unchanged.

Bug-id queries are served by BM25 direct-lookup, so source `bugtracker` is not
embedded.

**Incremental by default**: reuses the chunk vectors of any doc whose
`content_hash` is unchanged from the existing index, and only embeds new/changed
docs (dropping deleted ones). This is what the ingesters call after a run so the
index stays fresh without a full ~10 min re-embed. `--full` forces a rebuild.

  pipeline/.venv/bin/python scripts/build_embeddings.py            # incremental
  pipeline/.venv/bin/python scripts/build_embeddings.py --full     # full rebuild
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.request
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

# Chunking: ~3000 chars ≈ 750 tokens (nomic-embed ctx is 2048), overlap bridges
# answers that straddle a boundary, cap bounds the huge docs (RFCs, max 1.6 MB).
# size*cap covers ~21.9 KB/doc — past every concept-miss gold the eval flagged.
CHUNK_SIZE = 3000
CHUNK_OVERLAP = 300
CHUNK_CAP = 8


def _chunks(content: str) -> list[str]:
    """Split content into overlapping windows; always at least one chunk."""
    content = content or ""
    if len(content) <= CHUNK_SIZE:
        return [content]
    step = CHUNK_SIZE - CHUNK_OVERLAP
    out: list[str] = []
    start = 0
    while start < len(content) and len(out) < CHUNK_CAP:
        out.append(content[start : start + CHUNK_SIZE])
        start += step
    return out


def _embed(texts: list[str]) -> np.ndarray:
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        e = np.array(json.load(r)["embeddings"], dtype=np.float32)
    e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
    return e


def _corpus() -> list[tuple[str, str, str, str, str]]:
    """(doc_id, source, content_hash, title, content) for the embedded corpus."""
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return c.execute(
        "SELECT doc_id, source, coalesce(content_hash,''), coalesce(title,''), "
        "coalesce(content,'') FROM documents WHERE source != 'bugtracker' ORDER BY id"
    ).fetchall()


def _load_existing() -> tuple[dict[str, tuple[str, np.ndarray]], None] | tuple[None, None]:
    """Return {doc_id: (content_hash, chunk_vecs)} from the on-disk index, or (None,)."""
    if not (OUT_JSON.exists() and OUT_F32.exists()):
        return None, None
    try:
        meta = json.loads(OUT_JSON.read_text())
        docs = meta.get("docs")
        if not docs or meta.get("dim") != DIM:
            return None, None  # old flat format or dim drift → full rebuild
        buf = np.frombuffer(OUT_F32.read_bytes(), dtype=np.float32)
        mat = buf.reshape(-1, DIM)
        prev: dict[str, tuple[str, np.ndarray]] = {}
        row = 0
        for d in docs:
            n = int(d["n"])
            prev[d["d"]] = (d["h"], mat[row : row + n])
            row += n
        return prev, None
    except Exception as exc:  # noqa: BLE001 — any corruption → safe full rebuild
        print(f"  (existing index unreadable: {exc} — full rebuild)")
        return None, None


def incremental_build(*, full: bool = False, quiet: bool = False) -> dict:
    """Build (or refresh) the dense index. Returns stats; best-effort on Ollama errors.

    Reuses chunk vectors for unchanged content_hash; embeds only new/changed docs.
    """
    def say(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    rows = _corpus()
    prev = ({}, None)[0] if full else (_load_existing()[0] or {})
    t0 = time.time()

    docs_meta: list[dict] = []
    vec_blocks: list[np.ndarray] = []
    reused = embedded = 0
    pending_texts: list[str] = []          # chunk texts awaiting embedding
    pending_slots: list[int] = []          # index into vec_blocks for each pending doc

    def flush() -> None:
        nonlocal pending_texts, pending_slots
        if not pending_texts:
            return
        e = _embed(pending_texts)
        # scatter the batch's chunks back to their docs (slots hold per-doc lengths)
        off = 0
        for slot, n in pending_slots:
            vec_blocks[slot] = e[off : off + n]
            off += n
        pending_texts, pending_slots = [], []

    say(f"indexing {len(rows)} docs ({'full' if full else 'incremental'})…")
    for doc_id, source, chash, title, content in rows:
        chunks = _chunks(content)
        cached = prev.get(doc_id)
        slot = len(vec_blocks)
        if cached and cached[0] == chash and cached[1].shape[0] == len(chunks):
            vec_blocks.append(cached[1])           # reuse
            reused += 1
        else:
            vec_blocks.append(None)                # placeholder, fill on flush
            texts = [f"search_document: {title}\n{ck}" for ck in chunks]
            pending_texts.extend(texts)
            pending_slots.append((slot, len(texts)))
            embedded += 1
            if len(pending_texts) >= BATCH:
                flush()
        docs_meta.append({"d": doc_id, "s": source, "h": chash, "n": len(chunks)})
        if not quiet and (reused + embedded) % 5000 == 0:
            say(f"  {reused + embedded}/{len(rows)} (embedded {embedded}, reused {reused})")
    flush()

    vecs = np.ascontiguousarray(np.vstack(vec_blocks), dtype=np.float32)
    assert vecs.shape[1] == DIM
    OUT_F32.write_bytes(vecs.tobytes())
    OUT_JSON.write_text(
        json.dumps(
            {
                "model": MODEL,
                "dim": DIM,
                "chunked": True,
                "chunk_params": {"size": CHUNK_SIZE, "overlap": CHUNK_OVERLAP, "cap": CHUNK_CAP},
                "n_chunks": int(vecs.shape[0]),
                "docs": docs_meta,
            }
        )
    )
    stats = {
        "docs": len(rows),
        "chunks": int(vecs.shape[0]),
        "embedded": embedded,
        "reused": reused,
        "mb": round(vecs.nbytes / 1e6, 1),
        "secs": round(time.time() - t0, 1),
    }
    say(
        f"wrote {OUT_F32.name} ({stats['mb']} MB, {stats['chunks']} chunks) — "
        f"embedded {embedded}, reused {reused} in {stats['secs']}s"
    )
    return stats


def refresh_index_quiet() -> None:
    """Post-ingest hook for the ingesters: best-effort incremental refresh.

    Never raises — if Ollama is down the index is simply left stale (the webapp
    falls back to BM25-only), mirroring the retriever's graceful degradation.
    """
    try:
        s = incremental_build(quiet=True)
        print(f"  [embeddings] refreshed: embedded {s['embedded']}, reused {s['reused']} "
              f"({s['chunks']} chunks, {s['secs']}s)")
    except (urllib.error.URLError, OSError) as exc:
        print(f"  [embeddings] refresh skipped (Ollama unavailable: {exc}); "
              f"run scripts/build_embeddings.py when it's up")
    except Exception as exc:  # noqa: BLE001 — never fail an ingest over the index
        print(f"  [embeddings] refresh skipped (unexpected: {exc})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="force a full re-embed (ignore cache)")
    args = ap.parse_args()
    incremental_build(full=args.full)


if __name__ == "__main__":
    main()
