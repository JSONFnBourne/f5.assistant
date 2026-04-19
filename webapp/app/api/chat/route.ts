import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';
import MiniSearch from 'minisearch';
import { searchDocuments } from '@/lib/db';

export const maxDuration = 300;

const OLLAMA_URL = process.env.OLLAMA_URL || 'http://127.0.0.1:11434';
const OLLAMA_MODEL = process.env.OLLAMA_MODEL || 'llama3.2:latest';

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

        const dbResults = await searchDocuments(query, 5);

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
            const ollamaRes = await fetch(`${OLLAMA_URL}/api/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model: OLLAMA_MODEL,
                    messages: ollamaMessages,
                    stream: true,
                    options: {
                        num_ctx: 4096,
                        temperature: 0.7,
                        top_k: 40,
                        top_p: 0.9
                    }
                })
            });

            if (!ollamaRes.ok) {
                let errMsg = "Local LLM (Ollama) returned an error.";
                try {
                    const errData = await ollamaRes.json();
                    if (errData.error) errMsg = `Ollama Error: ${errData.error}`;
                } catch (e) { }
                throw new Error(errMsg);
            }

            if (!ollamaRes.body) throw new Error("No response body from Ollama.");

            const stream = new ReadableStream({
                async start(controller) {
                    const reader = ollamaRes.body!.getReader();
                    const decoder = new TextDecoder();
                    try {
                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;
                            const chunk = decoder.decode(value, { stream: true });
                            const lines = chunk.split('\n').filter(l => l.trim() !== '');
                            for (const line of lines) {
                                try {
                                    const parsed = JSON.parse(line);
                                    if (parsed.message?.content) {
                                        controller.enqueue(new TextEncoder().encode(parsed.message.content));
                                    }
                                } catch (e) { }
                            }
                        }
                    } catch (e) {
                        console.error("Stream reading error:", e);
                    } finally {
                        controller.close();
                    }
                }
            });

            return new Response(stream, {
                headers: {
                    'Content-Type': 'text/plain; charset=utf-8',
                    'Cache-Control': 'no-cache, no-transform',
                }
            });

        } catch (ollamaErr: any) {
            console.error("Failed to reach Ollama:", ollamaErr);
            return new Response(
                `Error communicating with local Ollama. Ensure Ollama is installed and running on localhost.`,
                { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } }
            );
        }

    } catch (error: any) {
        console.error(error);
        console.error('chat route unhandled error:', error);
        return new Response('Internal server error.', { status: 500, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
    }
}
