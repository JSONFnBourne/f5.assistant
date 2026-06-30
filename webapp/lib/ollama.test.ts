import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  streamOllamaChat, ollamaChat, isOllamaConnectionError, OllamaError,
} from './ollama';

// ── helpers ────────────────────────────────────────────────────────────────
const enc = new TextEncoder();

// Build a ReadableStream that emits the given byte chunks, one per pull — so
// the consumer sees exactly the chunk boundaries we choose.
function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(chunks[i++]);
      else controller.close();
    },
  });
}

// Mock the upstream Ollama fetch with a streaming body assembled from string chunks.
function mockStreamingFetch(strChunks: string[]) {
  const body = streamOf(strChunks.map(s => enc.encode(s)));
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, body }) as unknown as Response));
}

// Drain streamOllamaChat's output back into a string.
async function collect(stream: ReadableStream<Uint8Array>): Promise<string> {
  const reader = stream.getReader();
  const dec = new TextDecoder();
  let out = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    out += dec.decode(value, { stream: true });
  }
  return out + dec.decode();
}

const PARAMS = { messages: [{ role: 'user', content: 'hi' }] };

afterEach(() => vi.unstubAllGlobals());

// ── the chunk-boundary bug this module exists to fix ─────────────────────────
describe('streamOllamaChat — NDJSON carry-over buffering', () => {
  it('reassembles a JSON object split across chunk boundaries', async () => {
    // "Hello" object is cut mid-line between two network reads.
    mockStreamingFetch([
      '{"message":{"content":"Hel',
      'lo"}}\n{"message":{"content":" world"}}\n',
    ]);
    expect(await collect(await streamOllamaChat(PARAMS))).toBe('Hello world');
  });

  it('emits multiple objects packed into a single chunk', async () => {
    mockStreamingFetch(['{"message":{"content":"a"}}\n{"message":{"content":"b"}}\n']);
    expect(await collect(await streamOllamaChat(PARAMS))).toBe('ab');
  });

  it('flushes a final object that has no trailing newline', async () => {
    mockStreamingFetch(['{"message":{"content":"end"}}']);
    expect(await collect(await streamOllamaChat(PARAMS))).toBe('end');
  });

  it('skips malformed lines and content-less frames', async () => {
    mockStreamingFetch([
      'not json\n',
      '{"done":false}\n',                          // no message.content
      '{"message":{"content":"ok"}}\n',
      '{"message":{}}\n',                           // empty message
      '{"done":true,"total_duration":123}\n',      // final stats frame
    ]);
    expect(await collect(await streamOllamaChat(PARAMS))).toBe('ok');
  });

  it('ignores blank lines', async () => {
    mockStreamingFetch(['\n', '  \n', '{"message":{"content":"x"}}\n', '\n']);
    expect(await collect(await streamOllamaChat(PARAMS))).toBe('x');
  });

  it('survives a multi-byte UTF-8 char split across chunks', async () => {
    const bytes = enc.encode('{"message":{"content":"héllo"}}\n'); // é = 0xC3 0xA9
    const cut = bytes.indexOf(0xc3) + 1;                            // between é's two bytes
    mockStreamingFetch([]);   // replaced below with raw byte chunks
    vi.stubGlobal('fetch', vi.fn(async () =>
      ({ ok: true, body: streamOf([bytes.slice(0, cut), bytes.slice(cut)]) }) as unknown as Response));
    expect(await collect(await streamOllamaChat(PARAMS))).toBe('héllo');
  });
});

// ── error paths ──────────────────────────────────────────────────────────────
describe('streamOllamaChat / ollamaChat — error handling', () => {
  it('throws OllamaError with the upstream error message on a non-200', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 500, statusText: 'Internal Server Error',
      json: async () => ({ error: 'model not found' }),
    }) as unknown as Response));
    await expect(streamOllamaChat(PARAMS)).rejects.toThrow(OllamaError);
    await expect(streamOllamaChat(PARAMS)).rejects.toThrow(/model not found/);
  });

  it('maps a connection failure to a friendly OllamaError', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('fetch failed'); }));
    await expect(streamOllamaChat(PARAMS)).rejects.toThrow(/Cannot connect to Ollama/);
  });

  it('throws when a 200 response has no body', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, body: null }) as unknown as Response));
    await expect(streamOllamaChat(PARAMS)).rejects.toThrow(/No response body/);
  });

  it('ollamaChat returns message.content from a non-streaming response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, json: async () => ({ message: { content: 'the answer' } }),
    }) as unknown as Response));
    expect(await ollamaChat(PARAMS)).toBe('the answer');
  });

  it('ollamaChat returns empty string when content is absent', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, json: async () => ({ done: true }),
    }) as unknown as Response));
    expect(await ollamaChat(PARAMS)).toBe('');
  });
});

describe('isOllamaConnectionError', () => {
  it('recognises fetch/ECONNREFUSED failures, not other errors', () => {
    expect(isOllamaConnectionError(new Error('fetch failed'))).toBe(true);
    expect(isOllamaConnectionError(new Error('connect ECONNREFUSED 127.0.0.1:11434'))).toBe(true);
    expect(isOllamaConnectionError(new Error('some 500 error'))).toBe(false);
  });
});
