import { NextRequest, NextResponse } from 'next/server';

export const maxDuration = 300;

const BACKEND_URL = process.env.FASTAPI_BACKEND_URL || 'http://127.0.0.1:8000';

export async function POST(req: NextRequest) {
    try {
        // Expect a raw octet-stream body. Python multipart parsing was the
        // upload bottleneck (GIL-bound, < 1 MB/s); we now stream bytes
        // straight to the backend and pass the original filename in a header.
        const clientContentType = req.headers.get('content-type') || '';
        if (!clientContentType.startsWith('application/octet-stream')) {
            return NextResponse.json(
                { error: 'Expected application/octet-stream upload.' },
                { status: 415 }
            );
        }

        const filename = req.headers.get('x-filename') || '';
        if (!filename) {
            return NextResponse.json(
                { error: 'Missing X-Filename header.' },
                { status: 400 }
            );
        }

        const forwardHeaders: Record<string, string> = {
            'content-type': 'application/octet-stream',
            'x-filename': filename,
        };
        const contentLength = req.headers.get('content-length');
        if (contentLength) forwardHeaders['content-length'] = contentLength;

        const backendRes = await fetch(`${BACKEND_URL}/api/analyze`, {
            method: 'POST',
            headers: forwardHeaders,
            body: req.body as any,
            duplex: 'half',
        } as RequestInit);

        if (!backendRes.ok) {
            const errText = await backendRes.text();
            return NextResponse.json(
                { error: `Backend returned ${backendRes.status}: ${errText}` },
                { status: backendRes.status }
            );
        }

        // Stream NDJSON progress + final result straight through to the client.
        // backendRes.body is a ReadableStream<Uint8Array> once we don't consume it.
        return new Response(backendRes.body, {
            status: 200,
            headers: {
                'Content-Type': backendRes.headers.get('content-type') || 'application/x-ndjson',
                'Cache-Control': 'no-cache, no-transform',
            },
        });

    } catch (err: any) {
        console.error("Proxy error in /api/analyze:", err);
        return NextResponse.json({ error: 'Internal proxy error' }, { status: 500 });
    }
}
