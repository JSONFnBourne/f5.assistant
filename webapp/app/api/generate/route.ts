import { NextRequest, NextResponse } from 'next/server';
import { ollamaChat, OllamaError, isOllamaConnectionError, DEFAULT_TIMEOUT_MS } from '@/lib/ollama';

export const maxDuration = 300;

const VALID_TMOS_VERSIONS = new Set(['14.x', '15.x', '16.x', '17.x']);
const VALID_PROTOCOLS = new Set([
    'HTTP', 'HTTPS (ClientSSL)', 'TCP', 'UDP', 'DNS',
    'SSL (ServerSSL)', 'SIP', 'WebSocket', 'ASM',
]);

export async function POST(req: NextRequest) {
    try {
        const body = await req.json();
        const { skeleton, tmosVersion, protocol, dependencies } = body;

        if (!skeleton || typeof skeleton !== 'string') {
            return NextResponse.json({ error: 'skeleton is required' }, { status: 400 });
        }
        if (skeleton.length > 32_768) {
            return NextResponse.json({ error: 'skeleton too large' }, { status: 400 });
        }

        const safeVersion = VALID_TMOS_VERSIONS.has(tmosVersion) ? tmosVersion : '17.x';
        const safeProtocol = VALID_PROTOCOLS.has(protocol) ? protocol : 'TCP';
        const safeDeps = typeof dependencies === 'string'
            ? dependencies.slice(0, 500).replace(/[<>]/g, '')
            : '';

        const systemPrompt = `You are an expert F5 BIG-IP iRule developer specialising in TMOS ${safeVersion}.

The user has built an iRule skeleton using a GUI builder. Your task is to:
1. Complete any placeholder values with sensible, production-ready Tcl
2. Ensure all syntax is correct for TMOS ${safeVersion}
3. Add a single RULE_INIT block (if not already present) with set static::debug 0
4. Add debug logging inside each event using: if { $static::debug } { log local0. "..." }
5. Add brief inline comments explaining non-obvious logic
6. Output ONLY the complete, runnable iRule code — no explanation, no markdown fences, no preamble

Protocol context: ${safeProtocol}
${safeDeps ? `Dependencies/notes: ${safeDeps}` : ''}`;

        // Shared client: non-streaming, 180s timeout, aborts if the client
        // disconnects (req.signal) so abandoned generations stop on the GPU.
        const fullText = await ollamaChat({
            messages: [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: skeleton.slice(0, 32_768) }
            ],
            options: {
                num_ctx: 4096,
                temperature: 0.3,
                top_k: 40,
                top_p: 0.9
            },
            signal: req.signal,
            timeoutMs: DEFAULT_TIMEOUT_MS,
        });

        // Strip any accidental markdown code fences the model might add
        const code = fullText
            .replace(/^```(?:tcl)?\s*/i, '')
            .replace(/\s*```\s*$/, '')
            .trim();

        return NextResponse.json({ code });

    } catch (error) {
        console.error('Generate route error:', error);
        if (error instanceof OllamaError) {
            return NextResponse.json({ error: error.message }, { status: 503 });
        }
        if (error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
            return NextResponse.json({
                error: `Generation timed out after ${Math.round(DEFAULT_TIMEOUT_MS / 1000)}s or was cancelled.`
            }, { status: 503 });
        }
        const msg = error instanceof Error ? error.message : 'Unknown error';
        return NextResponse.json({
            error: isOllamaConnectionError(error)
                ? 'Cannot connect to Ollama. Ensure "ollama serve" is running.'
                : `Local LLM issue: ${msg}`
        }, { status: 503 });
    }
}
