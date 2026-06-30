/**
 * Dense-retrieval support for the hybrid knowledge retriever.
 *
 * Loads the prebuilt embedding index (db/knowledge_vec.{json,f32} — produced by
 * scripts/build_embeddings.py) into memory once, embeds queries via Ollama at
 * request time, and does brute-force cosine KNN. Everything here is best-effort:
 * if the index file or Ollama is unavailable, callers fall back to BM25-only.
 */
import fs from 'fs';
import path from 'path';

const OLLAMA_URL = process.env.OLLAMA_URL || 'http://127.0.0.1:11434';
const EMBED_MODEL = process.env.EMBED_MODEL || 'nomic-embed-text';
const DIM = 768;

function indexBase(): string {
  return process.env.KSI_VEC_PATH || path.join(process.cwd(), '..', 'db', 'knowledge_vec');
}

interface VecIndex {
  docIds: string[];
  sources: string[];
  dim: number;
  count: number;
  vectors: Float32Array; // count * dim, row-major, L2-normalized
}

// undefined = not yet attempted, null = unavailable (load failed)
let _index: VecIndex | null | undefined;

function loadIndex(): VecIndex | null {
  if (_index !== undefined) return _index;
  try {
    const base = indexBase();
    const meta = JSON.parse(fs.readFileSync(`${base}.json`, 'utf-8'));
    const buf = fs.readFileSync(`${base}.f32`);
    const vectors = new Float32Array(buf.buffer, buf.byteOffset, buf.byteLength / 4);
    if (vectors.length !== meta.count * meta.dim) throw new Error('index size mismatch');
    _index = { docIds: meta.doc_ids, sources: meta.sources, dim: meta.dim, count: meta.count, vectors };
  } catch (err) {
    console.error('Dense index unavailable (hybrid disabled):', err instanceof Error ? err.message : err);
    _index = null;
  }
  return _index;
}

/** Test hook: drop the cached index so a new KSI_VEC_PATH is picked up. */
export function _resetIndexCache(): void {
  _index = undefined;
}

export function denseAvailable(): boolean {
  return loadIndex() !== null;
}

function normalize(v: Float32Array): Float32Array {
  let n = 0;
  for (let i = 0; i < v.length; i++) n += v[i] * v[i];
  n = Math.sqrt(n) + 1e-9;
  for (let i = 0; i < v.length; i++) v[i] /= n;
  return v;
}

/** Embed a query via Ollama; returns an L2-normalized vector, or null on any failure. */
export async function embedQuery(text: string): Promise<Float32Array | null> {
  try {
    const res = await fetch(`${OLLAMA_URL}/api/embed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: EMBED_MODEL, input: [`search_query: ${text}`] }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    const v = data?.embeddings?.[0];
    if (!Array.isArray(v) || v.length !== DIM) return null;
    return normalize(Float32Array.from(v));
  } catch {
    return null;
  }
}

/** Cosine KNN over the index (vectors normalized → dot product), filtered to `sources`. */
export function denseSearch(qvec: Float32Array, sources: string[] | undefined, k: number): string[] {
  const idx = loadIndex();
  if (!idx) return [];
  const allow = sources ? new Set(sources) : null;
  const { vectors, docIds, sources: src, dim, count } = idx;
  const scored: { i: number; s: number }[] = [];
  for (let i = 0; i < count; i++) {
    if (allow && !allow.has(src[i])) continue;
    const off = i * dim;
    let dot = 0;
    for (let j = 0; j < dim; j++) dot += vectors[off + j] * qvec[j];
    scored.push({ i, s: dot });
  }
  scored.sort((a, b) => b.s - a.s);
  return scored.slice(0, k).map((x) => docIds[x.i]);
}

/** Reciprocal Rank Fusion of several ranked doc_id lists. */
export function rrf(rankLists: string[][], k = 60): string[] {
  const score = new Map<string, number>();
  for (const list of rankLists) {
    for (let r = 0; r < list.length; r++) {
      score.set(list[r], (score.get(list[r]) ?? 0) + 1 / (k + r + 1));
    }
  }
  return [...score.entries()].sort((a, b) => b[1] - a[1]).map(([d]) => d);
}
