import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import MiniSearch from 'minisearch';
import { searchDocuments } from '@/lib/db';
import { streamOllamaChat, OllamaError } from '@/lib/ollama';

export const maxDuration = 300;

let searchIndex: MiniSearch | null = null;

function loadIndex() {
    if (searchIndex) return searchIndex;
    try {
        const indexPath = path.join(process.cwd(), 'public', 'search-index.json');
        const indexData = fs.readFileSync(indexPath, 'utf-8');
        searchIndex = MiniSearch.loadJSON(indexData, {
            fields: ['text', 'heading', 'path'],
            storeFields: ['path', 'heading', 'text']
        });
        return searchIndex;
    } catch (e) {
        console.error("Failed to load search index:", e);
        return null;
    }
}

const MAX_MESSAGES = 50;
const MAX_MESSAGE_CHARS = 32_768;

export async function POST(req: Request) {
    try {
        const body = await req.json();

        if (!Array.isArray(body?.messages)) {
            return NextResponse.json({ error: 'messages must be an array' }, { status: 400 });
        }

        const messages: { role: string; content: string }[] = body.messages
            .slice(-MAX_MESSAGES)
            .filter((m: any) => m && typeof m.role === 'string' && typeof m.content === 'string')
            .map((m: any) => ({ role: m.role, content: m.content.slice(0, MAX_MESSAGE_CHARS) }));

        const lastMessage = messages[messages.length - 1];

        if (!lastMessage || lastMessage.role !== 'user') {
            return NextResponse.json({
                role: 'assistant',
                content: "I didn't receive a valid query.",
                timestamp: Date.now()
            });
        }

        let query = lastMessage.content;
        let isCodeAnalysis = false;
        const lowerQuery = query.toLowerCase();

        if (lowerQuery.includes('explain') || lowerQuery.includes('what does this') || lowerQuery.includes('analyze') || lowerQuery.includes('add comments') || lowerQuery.includes('what this irule does')) {
            isCodeAnalysis = true;
            const distinctivePatterns = query.match(/[A-Za-z]+::[a-zA-Z]+/g) || [];
            if (distinctivePatterns.length > 0) {
                query = [...new Set(distinctivePatterns)].join(' ');
            } else {
                const rawTokens = query.match(/\b[A-Za-z_]+\b/g) || [];
                const stopWords = new Set([
                    'what', 'does', 'this', 'irule', 'tell', 'explain', 'analyze', 'comments',
                    'needed', 'when', 'string', 'pool', 'switch', 'tolower', 'default', 'return',
                    'elseif', 'if', 'else', 'set', 'log', 'local', 'the', 'a', 'an', 'and', 'or',
                    'to', 'for', 'in', 'of', 'on'
                ]);
                const keywords = rawTokens.filter((t: string) => !stopWords.has(t.toLowerCase())).slice(0, 5);
                query = keywords.join(' ');
            }
        }

        const index = loadIndex();

        if (!index) {
            return NextResponse.json({
                role: 'assistant',
                content: "Local search index not found. Run `npm run prebuild` to build it.",
                timestamp: Date.now()
            });
        }

        let results = index.search(query, { prefix: true, combineWith: 'AND' });
        if (results.length === 0) {
            const looseResults = index.search(query, { prefix: true, combineWith: 'OR' });
            if (looseResults.length > 0) results.push(...looseResults);
        }

        // Retrieval is an optional augment for this route — a knowledge.db
        // failure must not kill the chat (and must not be reported as an
        // Ollama problem). Degrade to minisearch-only context.
        let dbResults: Awaited<ReturnType<typeof searchDocuments>> = [];
        try {
            dbResults = await searchDocuments(query, 5);
        } catch (dbErr) {
            console.warn('chat route: knowledge.db retrieval failed, continuing without it:', dbErr);
        }

        const topResults = results.slice(0, 3);
        let contextText = '';

        topResults.forEach((res, i) => {
            if (res.path.endsWith('.tcl')) {
                contextText += `[Code Snippet ${i+1} from ${res.path}]:\n\`\`\`tcl\n${res.text}\n\`\`\`\n\n`;
            } else {
                contextText += `[Documentation ${i+1} from ${res.path}]:\n${res.text.substring(0, 1000)}\n\n`;
            }
        });

        dbResults.forEach((res, i) => {
            const contentSnippet = (res.content || res.snippet || "").substring(0, 1500);
            contextText += `[Expert Knowledge ${i + 1} (${res.source}) from ${res.title}]:\n${contentSnippet}\n\n`;
        });

        const ollamaMessages = messages.map((m: any, idx: number) => {
            if (idx === messages.length - 1 && contextText) {
                return {
                    role: m.role,
                    content: `Context information from F5 experts and documentation:\n\n${contextText}\n\nGiven the context above, please answer the following user query expertly. Prioritize the provided context, but use your internal knowledge for syntax and best practices if the context is silent.\n\n${m.content}`
                };
            }
            return { role: m.role, content: m.content };
        });

        ollamaMessages.unshift({
            role: 'system',
            content: "You are an expert F5 Assistant and Senior Network Engineer. Answer questions about F5 architecture (TMOS, LTM, DNS, Security) and protocol standards (RFCs). Use the provided context if relevant. If asking for code or configuration, provide valid F5 iRule (TCL) or tmsh syntax. DO NOT invent new commands. If the answer is not in the context or your training data, be honest about it."
        });

        try {
            // Shared client: NDJSON framing, chunk-boundary buffering, timeout,
            // and abort-on-disconnect (req.signal + the stream's cancel()).
            const stream = await streamOllamaChat({
                messages: ollamaMessages,
                options: {
                    num_ctx: 4096,
                    temperature: 0.7,
                    top_k: 40,
                    top_p: 0.9
                },
                signal: req.signal,
            });

            return new Response(stream, {
                headers: {
                    'Content-Type': 'text/plain; charset=utf-8',
                    'Cache-Control': 'no-cache, no-transform',
                }
            });

        } catch (ollamaErr: unknown) {
            console.error('Failed to reach Ollama:', ollamaErr);
            const detail = ollamaErr instanceof OllamaError
                ? ollamaErr.message
                : 'Error communicating with local Ollama. Ensure Ollama is installed and running on localhost.';
            return new Response(detail, {
                status: 503,
                headers: { 'Content-Type': 'text/plain; charset=utf-8' },
            });
        }

    } catch (error: unknown) {
        console.error('chat route unhandled error:', error);
        return new Response('Internal server error.', { status: 500, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
    }
}
