import { NextRequest, NextResponse } from 'next/server';

const BACKEND_URL = process.env.FASTAPI_BACKEND_URL || 'http://127.0.0.1:8000';

export async function GET(
    req: NextRequest,
    context: { params: Promise<{ id: string; path: string[] }> }
) {
    const { id, path } = await context.params;
    const fullPath = '/' + path.map(encodeURIComponent).join('/');
    const url = `${BACKEND_URL}/api/qkview/${encodeURIComponent(id)}/apps${fullPath}`;
    try {
        const backendRes = await fetch(url, { headers: { accept: 'application/json' } });
        const body = await backendRes.text();
        return new Response(body, {
            status: backendRes.status,
            headers: { 'Content-Type': backendRes.headers.get('content-type') || 'application/json' },
        });
    } catch (err) {
        console.error(`Proxy error in /api/qkview/${id}/apps${fullPath}:`, err);
        return NextResponse.json({ error: 'Internal proxy error' }, { status: 500 });
    }
}
