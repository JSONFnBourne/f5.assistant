import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { rrf, embedQuery, denseSearch, denseAvailable, _resetIndexCache } from './embeddings';

// ── RRF is pure ──────────────────────────────────────────────────────────────
describe('rrf (reciprocal rank fusion)', () => {
  it('ranks a doc that is high in BOTH lists first', () => {
    const a = ['a', 'b', 'c'];
    const b = ['a', 'c', 'b']; // 'a' is rank 1 in both
    expect(rrf([a, b])[0]).toBe('a');
  });

  it('a doc in two lists beats a doc in only one', () => {
    const fused = rrf([['x', 'shared'], ['shared', 'y']]);
    expect(fused.indexOf('shared')).toBeLessThan(fused.indexOf('x'));
    expect(fused.indexOf('shared')).toBeLessThan(fused.indexOf('y'));
  });

  it('handles an empty list (degrades to the other ranking)', () => {
    expect(rrf([['a', 'b', 'c'], []])).toEqual(['a', 'b', 'c']);
  });
});

// ── query embedding (mocked Ollama) ──────────────────────────────────────────
describe('embedQuery', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('returns an L2-normalized 768-vector on success', async () => {
    const raw = Array.from({ length: 768 }, (_, i) => (i === 0 ? 3 : 0)); // [3,0,0,...]
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ embeddings: [raw] }) })));
    const v = await embedQuery('how to redirect');
    expect(v).not.toBeNull();
    expect(v!.length).toBe(768);
    expect(v![0]).toBeCloseTo(1.0, 5); // 3/3 after normalization
  });

  it('returns null on a non-OK response, wrong dim, or fetch failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false })));
    expect(await embedQuery('x')).toBeNull();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ embeddings: [[1, 2, 3]] }) })));
    expect(await embedQuery('x')).toBeNull(); // dim != 768
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('fetch failed'); }));
    expect(await embedQuery('x')).toBeNull();
  });
});

// ── dense KNN over a tiny fixture index ──────────────────────────────────────
describe('denseSearch + denseAvailable', () => {
  let base: string;

  function writeIndex(dim: number, docs: { doc_id: string; source: string; vec: number[] }[]) {
    fs.writeFileSync(`${base}.json`, JSON.stringify({
      model: 'test', dim, count: docs.length,
      doc_ids: docs.map((d) => d.doc_id), sources: docs.map((d) => d.source),
    }));
    const arr = new Float32Array(docs.length * dim);
    docs.forEach((d, i) => d.vec.forEach((v, j) => (arr[i * dim + j] = v)));
    fs.writeFileSync(`${base}.f32`, Buffer.from(arr.buffer));
    _resetIndexCache();
  }

  beforeEach(() => {
    base = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'vec-')), 'idx');
    process.env.KSI_VEC_PATH = base;
  });
  afterEach(() => {
    delete process.env.KSI_VEC_PATH;
    _resetIndexCache();
  });

  it('ranks by cosine and honours the source filter', () => {
    writeIndex(3, [
      { doc_id: 'd1', source: 'f5_kb', vec: [1, 0, 0] },
      { doc_id: 'd2', source: 'rfc', vec: [1, 0, 0] }, // identical vector but wrong source
      { doc_id: 'd3', source: 'f5_kb', vec: [0.8, 0.6, 0] },
    ]);
    const q = Float32Array.from([1, 0, 0]);
    const hits = denseSearch(q, ['f5_kb'], 5);
    expect(hits).toEqual(['d1', 'd3']); // d2 filtered out by source
  });

  it('with no source filter searches everything', () => {
    writeIndex(3, [
      { doc_id: 'd1', source: 'f5_kb', vec: [0, 1, 0] },
      { doc_id: 'd2', source: 'rfc', vec: [1, 0, 0] },
    ]);
    expect(denseSearch(Float32Array.from([1, 0, 0]), undefined, 5)[0]).toBe('d2');
  });

  it('reports available when the index loads', () => {
    writeIndex(3, [{ doc_id: 'd1', source: 'f5_kb', vec: [1, 0, 0] }]);
    expect(denseAvailable()).toBe(true);
  });

  it('degrades gracefully when the index is missing', () => {
    process.env.KSI_VEC_PATH = '/nonexistent/path/idx';
    _resetIndexCache();
    expect(denseAvailable()).toBe(false);
    expect(denseSearch(Float32Array.from([1, 0, 0]), undefined, 5)).toEqual([]);
  });
});
