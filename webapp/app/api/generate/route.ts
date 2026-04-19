import { NextRequest, NextResponse } from 'next/server';

export const maxDuration = 300;

const OLLAMA_URL = process.env.OLLAMA_URL || 'http://127.0.0.1:11434';
const OLLAMA_MODEL = process.env.OLLAMA_MODEL || 'llama3.2:latest';

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

        const ollamaRes = await fetch(`${OLLAMA_URL}/api/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: OLLAMA_MODEL,
                messages: [
                    { role: 'system', content: systemPrompt },
                    { role: 'user', content: skeleton.slice(0, 32_768) }
                ],
                stream: false,
                options: {
                    num_ctx: 4096,
                    temperature: 0.3,
                    top_k: 40,
                    top_p: 0.9
                }
            })
        });

        if (!ollamaRes.ok) {
            return NextResponse.json({
                error: ollamaRes.status >= 500 || ollamaRes.status === 0
                    ? 'Cannot connect to Ollama. Ensure "ollama serve" is running.'
                    : `Local LLM error: ${ollamaRes.statusText}`
            }, { status: 503 });
        }

        const ollamaJson = await ollamaRes.json();
        const fullText: string = ollamaJson?.message?.content ?? '';

        // Strip any accidental markdown code fences the model might add
        const code = fullText
            .replace(/^```(?:tcl)?\s*/i, '')
            .replace(/\s*```\s*$/, '')
            .trim();

        return NextResponse.json({ code });

    } catch (error) {
        console.error('Generate route error:', error);
        const msg = error instanceof Error ? error.message : 'Unknown error';
        const isConn = msg.includes('fetch failed') || msg.includes('ECONNREFUSED');
        return NextResponse.json({
            error: isConn
                ? 'Cannot connect to Ollama. Ensure "ollama serve" is running.'
                : `Local LLM issue: ${msg}`
        }, { status: 503 });
    }
}
