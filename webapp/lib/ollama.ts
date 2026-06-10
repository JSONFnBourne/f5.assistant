/**
 * Shared Ollama client for all LLM API routes.
 *
 * Centralizes the /api/chat call and the NDJSON stream-pump that was
 * previously duplicated in app/api/knowledge, app/api/chat, and
 * app/api/generate. Fixes the chunk-boundary bug in the old copies: a JSON
 * object straddling a network-chunk boundary was silently dropped because
 * each read was decoded and split('\n') independently. Here a persistent
 * string buffer carries the trailing partial line across reads.
 */

const OLLAMA_URL = process.env.OLLAMA_URL || 'http://127.0.0.1:11434';
const OLLAMA_MODEL = process.env.OLLAMA_MODEL || 'llama3.2:latest';

export const DEFAULT_STREAM_TIMEOUT_MS = 300_000;
export const DEFAULT_TIMEOUT_MS = 180_000;

export interface OllamaChatMessage {
    role: string;
    content: string;
}

export interface OllamaChatParams {
    messages: OllamaChatMessage[];
    /** Ollama runtime options (num_ctx, temperature, top_k, top_p, …). */
    options?: Record<string, number | string | boolean>;
    /** Defaults to OLLAMA_MODEL from the environment. */
    model?: string;
    /** Caller abort signal (e.g. req.signal) — composed with the timeout. */
    signal?: AbortSignal;
    /** Overall timeout in ms. Defaults: 300s streaming, 180s non-streaming. */
    timeoutMs?: number;
}

/** Errors raised while talking to Ollama (vs. retrieval / other layers). */
export class OllamaError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'OllamaError';
    }
}

export function isOllamaConnectionError(err: unknown): boolean {
    const msg = err instanceof Error ? err.message : String(err);
    return msg.includes('fetch failed') || msg.includes('ECONNREFUSED');
}

function composeSignal(signal: AbortSignal | undefined, timeoutMs: number): AbortSignal {
    const signals: AbortSignal[] = [AbortSignal.timeout(timeoutMs)];
    if (signal) signals.push(signal);
    return signals.length === 1 ? signals[0] : AbortSignal.any(signals);
}

async function ollamaFetch(params: OllamaChatParams, stream: boolean, timeoutMs: number): Promise<Response> {
    const composed = composeSignal(params.signal, params.timeoutMs ?? timeoutMs);
    let res: Response;
    try {
        res = await fetch(`${OLLAMA_URL}/api/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: params.model ?? OLLAMA_MODEL,
                messages: params.messages,
                stream,
                options: params.options,
            }),
            signal: composed,
        });
    } catch (err) {
        if (isOllamaConnectionError(err)) {
            throw new OllamaError('Cannot connect to Ollama. Ensure "ollama serve" is running.');
        }
        throw err;
    }

    if (!res.ok) {
        let msg = `Local LLM error: ${res.status} ${res.statusText}`;
        try {
            const errData = await res.json();
            if (errData?.error) msg = `Ollama error: ${errData.error}`;
        } catch { /* non-JSON error body — keep the status message */ }
        throw new OllamaError(msg);
    }
    return res;
}

/**
 * Non-streaming chat completion. Resolves to the assistant message content.
 * Throws OllamaError for connection / HTTP-level failures.
 */
export async function ollamaChat(params: OllamaChatParams): Promise<string> {
    const res = await ollamaFetch(params, false, DEFAULT_TIMEOUT_MS);
    const json = await res.json();
    return json?.message?.content ?? '';
}

/**
 * Streaming chat completion. Resolves to a ReadableStream of UTF-8 encoded
 * assistant text chunks (Ollama NDJSON framing removed). The stream's
 * cancel() cancels the upstream Ollama body, so a client disconnect stops
 * generation. Throws OllamaError before streaming starts for connection /
 * HTTP-level failures.
 */
export async function streamOllamaChat(params: OllamaChatParams): Promise<ReadableStream<Uint8Array>> {
    const res = await ollamaFetch(params, true, DEFAULT_STREAM_TIMEOUT_MS);
    if (!res.body) throw new OllamaError('No response body from Ollama.');

    const upstream = res.body.getReader();
    const encoder = new TextEncoder();
    const decoder = new TextDecoder();
    let buffer = '';
    let cancelled = false;

    const emitLine = (controller: ReadableStreamDefaultController<Uint8Array>, line: string) => {
        if (!line.trim()) return;
        try {
            const parsed = JSON.parse(line);
            if (parsed.message?.content) {
                controller.enqueue(encoder.encode(parsed.message.content));
            }
        } catch { /* malformed NDJSON line — skip */ }
    };

    return new ReadableStream<Uint8Array>({
        async start(controller) {
            try {
                while (!cancelled) {
                    const { done, value } = await upstream.read();
                    if (done) break;
                    // Persistent buffer: keep the trailing partial line across
                    // reads so JSON objects straddling chunk boundaries survive.
                    buffer += decoder.decode(value, { stream: true });
                    const parts = buffer.split('\n');
                    buffer = parts.pop() ?? '';
                    for (const part of parts) emitLine(controller, part);
                }
                if (!cancelled) {
                    buffer += decoder.decode();   // flush the decoder
                    if (buffer) emitLine(controller, buffer);
                }
            } catch (err) {
                if (!cancelled) console.error('Ollama stream error:', err);
            } finally {
                try { controller.close(); } catch { /* already closed/cancelled */ }
            }
        },
        cancel(reason) {
            cancelled = true;
            upstream.cancel(reason).catch(() => { /* upstream already gone */ });
        },
    });
}
