import { NextRequest, NextResponse } from 'next/server';
import { searchDocuments } from '@/lib/db';
import { classifyQuery, type QueryMode } from '@/lib/knowledgeClassifier';
import { streamOllamaChat, OllamaError, isOllamaConnectionError } from '@/lib/ollama';

export const maxDuration = 300;

// ── Mode-aware system prompts ─────────────────────────────────────────────

function buildSystemPrompt(mode: QueryMode, context: string): string {
    const sharedRules = `
Rules:
1. STRICT GROUNDING: Answer ONLY from the provided Context below. Do NOT use your general training data to supplement, expand, or invent information not present in the Context.
2. SCOPE: Answer ONLY what the user asked. Do not volunteer information about related topics, adjacent codes, other error classes, or additional categories that were not part of the question.
3. Cite sources (titles and URLs) only from the provided context.
4. All processing is LOCAL. Do not reference external internet sources.
5. Use Markdown code blocks only for real, verified syntax from the context — never write pseudo-code or invent configuration examples.
`;

    const modeIntro: Record<QueryMode, string> = {
        f5: `You are an expert F5 BIG-IP engineer and solutions architect.
Answer this question strictly in the context of F5 product configuration, behavior, and best practices.
Use only the F5 CloudDocs and iRules documentation context provided below.
Do NOT include generic protocol theory or RFC explanations unless they appear verbatim in the context.`,
        rfc: `You are an expert network engineer specializing in open protocol standards.
Answer this question strictly in the context of RFC standards and protocol mechanics.
Use only the RFC documentation context provided below.
Do NOT include any vendor-specific content (F5, Cisco, Juniper, or any other vendor), product configuration examples, or pseudo-code.
If the context contains vendor material, ignore it entirely.`,
        general: `You are an expert F5 BIG-IP engineer and network protocols specialist.
Draw from both F5 product documentation and RFC standards as relevant to exactly what was asked.
Use the documentation context provided below.
Do NOT include vendor-specific configuration examples unless they appear verbatim in the context.`,
    };

    return `${modeIntro[mode]}
${sharedRules}
Context:
${context}`;
}

// ── Sources per mode ──────────────────────────────────────────────────────

const MODE_SOURCES: Record<QueryMode, string[] | undefined> = {
    f5:      ['irules', 'clouddocs', 'f5_kb', 'f5_security', 'xc_techdocs', 'techdocs', 'community'],
    rfc:     ['rfc'],
    general: undefined,   // no filter — search all sources
};

// ── Route handler ─────────────────────────────────────────────────────────

export async function POST(req: NextRequest) {
    try {
        const { message } = await req.json();

        if (!message || typeof message !== 'string') {
            return NextResponse.json({ error: 'Message is required' }, { status: 400 });
        }
        if (message.length > 4_000) {
            return NextResponse.json({ error: 'Message exceeds 4,000 character limit' }, { status: 400 });
        }

        const mode = classifyQuery(message);
        const sources = MODE_SOURCES[mode];

        // Retrieval layer — failures here are NOT Ollama problems and must
        // not be reported as such.
        let finalResults;
        try {
            const resultLimit = mode === 'general' ? 8 : 5;
            const results = await searchDocuments(message, resultLimit, sources);
            finalResults = results.length > 0
                ? results
                : await searchDocuments(message, 5);
        } catch (err) {
            console.error('Knowledge retrieval error:', err);
            const msg = err instanceof Error ? err.message : 'Unknown retrieval error';
            return NextResponse.json({
                error: `Knowledge retrieval failed (not an LLM issue): ${msg}`
            }, { status: 500 });
        }

        const context = finalResults
            .map((r) => `[Source: ${r.title}] (${r.url})\n${r.content.substring(0, 1000)}...`)
            .join('\n\n---\n\n');

        let systemPrompt = buildSystemPrompt(mode, context);

        // When the user explicitly cites a K-article, prepend an authority note
        // so the LLM anchors its answer to that document rather than blending sources.
        const citedKNumbers = message.match(/\bk\d{4,}\b/gi);
        if (citedKNumbers && citedKNumbers.length > 0) {
            const kList = citedKNumbers.map((k: string) => k.toUpperCase()).join(', ');
            systemPrompt = `AUTHORITY NOTE: The user explicitly referenced ${kList}. If this document is present in the Context below, base your answer primarily on its content and cite it as the principal source. Do not draw from other context sources unless the cited document is silent on the specific point asked.\n\n` + systemPrompt;
        }

        // LLM layer — shared client handles NDJSON framing, chunk-boundary
        // buffering, timeout, and abort-on-disconnect (req.signal).
        const llmStream = await streamOllamaChat({
            messages: [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: message }
            ],
            options: {
                num_ctx: 8192,
                temperature: 0.3,
                top_k: 40,
                top_p: 0.9
            },
            signal: req.signal,
        });

        const enc = new TextEncoder();
        const upstream = llmStream.getReader();

        // Stream protocol:
        //   Line 1: JSON metadata  {"__meta":true,"mode":"f5","sources":[...]}
        //   Lines 2+: raw LLM text chunks (no framing)
        const stream = new ReadableStream<Uint8Array>({
            start(controller) {
                const meta = { __meta: true, mode, sources: finalResults.map(r => ({ title: r.title, url: r.url })) };
                controller.enqueue(enc.encode(JSON.stringify(meta) + '\n'));
            },
            async pull(controller) {
                const { done, value } = await upstream.read();
                if (done) {
                    controller.close();
                } else {
                    controller.enqueue(value);
                }
            },
            // Client disconnected — cancel the Ollama stream so generation
            // stops burning GPU on an abandoned question.
            cancel(reason) {
                upstream.cancel(reason).catch(() => { /* upstream already gone */ });
            },
        });

        return new Response(stream, {
            headers: {
                'Content-Type': 'text/plain; charset=utf-8',
                'Cache-Control': 'no-cache, no-transform',
            }
        });

    } catch (error) {
        console.error('Knowledge route error:', error);
        if (error instanceof OllamaError) {
            return NextResponse.json({ error: error.message }, { status: 503 });
        }
        const msg = error instanceof Error ? error.message : 'Unknown error';
        if (isOllamaConnectionError(error)) {
            return NextResponse.json({
                error: 'Cannot connect to Ollama. Ensure "ollama serve" is running.'
            }, { status: 503 });
        }
        return NextResponse.json({ error: `Local LLM issue: ${msg}` }, { status: 503 });
    }
}
