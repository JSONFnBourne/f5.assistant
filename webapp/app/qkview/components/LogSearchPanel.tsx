'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Search, Loader2, ChevronLeft, ChevronRight, AlertTriangle, ScrollText } from 'lucide-react';

const PAGE_SIZE = 50;

interface LogEntry {
    timestamp: string;
    host: string | null;
    process: string | null;
    severity: string;
    msg_code: string | null;
    source: string;
    message: string;
    raw_line: string;
}

interface Facet {
    value: string;
    count: number;
}

interface LogsResponse {
    total: number;
    limit: number;
    offset: number;
    entries: LogEntry[];
    capped: boolean;
    facets?: {
        severities: Facet[];
        processes: Facet[];
        sources: Facet[];
    };
}

function severityColor(severity: string): string {
    if (severity === 'emerg' || severity === 'alert' || severity === 'crit' || severity === 'critical' || severity === 'err') {
        return 'text-red-400';
    }
    if (severity === 'warning') return 'text-amber-400';
    if (severity === 'notice') return 'text-blue-400';
    return 'text-slate-300';
}

export default function LogSearchPanel({ analysisId }: { analysisId: number }) {
    const [queryInput, setQueryInput] = useState('');
    const [query, setQuery] = useState('');
    const [severity, setSeverity] = useState('');
    const [processFilter, setProcessFilter] = useState('');
    const [offset, setOffset] = useState(0);
    const [data, setData] = useState<LogsResponse | null>(null);
    const [facets, setFacets] = useState<LogsResponse['facets'] | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [noIndex, setNoIndex] = useState(false);

    // Monotonic request id — stale responses from superseded fetches are
    // dropped instead of overwriting the latest results.
    const reqIdRef = useRef(0);
    const facetsLoadedRef = useRef(false);

    useEffect(() => {
        const reqId = ++reqIdRef.current;
        const controller = new AbortController();
        const params = new URLSearchParams();
        if (query) params.set('q', query);
        if (severity) params.set('severity', severity);
        if (processFilter) params.set('process', processFilter);
        params.set('limit', String(PAGE_SIZE));
        params.set('offset', String(offset));
        if (!facetsLoadedRef.current) params.set('facets', 'true');

        setLoading(true);
        setError(null);
        (async () => {
            try {
                const res = await fetch(`/api/qkview/${analysisId}/logs?${params.toString()}`, {
                    signal: controller.signal,
                });
                if (reqId !== reqIdRef.current) return;
                if (res.status === 404) {
                    let detail = '';
                    try {
                        detail = (await res.json())?.detail ?? '';
                    } catch { /* non-JSON 404 body */ }
                    if (detail.includes('No log index')) {
                        setNoIndex(true);
                    } else {
                        setError(detail || 'Analysis not found.');
                    }
                    setData(null);
                    return;
                }
                if (!res.ok) throw new Error(`Backend returned ${res.status}`);
                const body: LogsResponse = await res.json();
                if (reqId !== reqIdRef.current) return;
                setNoIndex(false);
                setData(body);
                if (body.facets) {
                    facetsLoadedRef.current = true;
                    setFacets(body.facets);
                }
            } catch (err: unknown) {
                if (controller.signal.aborted || reqId !== reqIdRef.current) return;
                setError(err instanceof Error ? err.message : 'Log search failed.');
                setData(null);
            } finally {
                if (reqId === reqIdRef.current) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, [analysisId, query, severity, processFilter, offset]);

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setOffset(0);
        setQuery(queryInput.trim());
    };

    const total = data?.total ?? 0;
    const shown = data?.entries.length ?? 0;
    const rangeStart = shown > 0 ? offset + 1 : 0;
    const rangeEnd = offset + shown;

    return (
        <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
            <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                <ScrollText className="w-5 h-5 text-amber-500" /> Log Search
                {data != null && !loading && (
                    <span className="text-xs font-normal text-slate-500 dark:text-slate-400">
                        {total.toLocaleString()} matching entr{total === 1 ? 'y' : 'ies'}
                    </span>
                )}
            </h3>

            {noIndex ? (
                <div className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded px-3 py-2">
                    <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                    <span>No log index is stored for this analysis — it predates the log-search feature. Re-run the analysis to enable searching.</span>
                </div>
            ) : (
                <>
                    <form onSubmit={handleSubmit} className="flex flex-wrap items-center gap-2 mb-4">
                        <div className="relative flex-1 min-w-[220px]">
                            <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                            <input
                                type="text"
                                value={queryInput}
                                onChange={(e) => setQueryInput(e.target.value)}
                                placeholder="Full-text search (e.g. pool member down, 01010028)…"
                                className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-amber-500"
                            />
                        </div>
                        <select
                            value={severity}
                            onChange={(e) => { setSeverity(e.target.value); setOffset(0); }}
                            className="text-sm py-1.5 px-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 focus:outline-none focus:ring-2 focus:ring-amber-500"
                        >
                            <option value="">All severities</option>
                            {(facets?.severities ?? []).map((f) => (
                                <option key={f.value} value={f.value}>{f.value} ({f.count.toLocaleString()})</option>
                            ))}
                        </select>
                        <select
                            value={processFilter}
                            onChange={(e) => { setProcessFilter(e.target.value); setOffset(0); }}
                            className="text-sm py-1.5 px-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 focus:outline-none focus:ring-2 focus:ring-amber-500"
                        >
                            <option value="">All processes</option>
                            {(facets?.processes ?? []).map((f) => (
                                <option key={f.value} value={f.value}>{f.value} ({f.count.toLocaleString()})</option>
                            ))}
                        </select>
                        <button
                            type="submit"
                            className="px-4 py-1.5 text-sm font-semibold rounded-lg bg-amber-500 hover:bg-amber-600 text-white disabled:opacity-50"
                            disabled={loading}
                        >
                            Search
                        </button>
                    </form>

                    {error && (
                        <div className="flex items-start gap-2 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded px-3 py-2 mb-4">
                            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                            <span>{error}</span>
                        </div>
                    )}

                    <div className="bg-black rounded border border-slate-800 max-h-[480px] overflow-y-auto font-mono text-xs">
                        {loading ? (
                            <div className="flex items-center gap-2 text-slate-400 p-4">
                                <Loader2 className="w-4 h-4 animate-spin" /> Searching log index…
                            </div>
                        ) : data && data.entries.length > 0 ? (
                            <table className="w-full text-left">
                                <thead className="text-slate-500 uppercase text-[10px] sticky top-0 bg-black">
                                    <tr>
                                        <th className="py-2 px-3 font-semibold">Timestamp</th>
                                        <th className="py-2 px-3 font-semibold">Severity</th>
                                        <th className="py-2 px-3 font-semibold">Process</th>
                                        <th className="py-2 px-3 font-semibold w-full">Message</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-800/60">
                                    {data.entries.map((entry, i) => (
                                        <tr key={`${entry.timestamp}-${offset}-${i}`} className="align-top">
                                            <td className="py-1.5 px-3 text-slate-500 whitespace-nowrap">{entry.timestamp}</td>
                                            <td className={`py-1.5 px-3 ${severityColor(entry.severity)}`}>{entry.severity}</td>
                                            <td className="py-1.5 px-3 text-slate-400 whitespace-nowrap">{entry.process || '—'}</td>
                                            <td className={`py-1.5 px-3 whitespace-pre-wrap break-all leading-relaxed ${severityColor(entry.severity)}`}>
                                                {entry.message || entry.raw_line}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        ) : !error ? (
                            <p className="text-slate-500 italic p-4">
                                {query || severity || processFilter
                                    ? 'No log entries match the current filters.'
                                    : 'No log entries indexed for this analysis.'}
                            </p>
                        ) : null}
                    </div>

                    <div className="flex items-center justify-between mt-3 text-xs text-slate-500 dark:text-slate-400">
                        <span>
                            {total > 0 ? `Showing ${rangeStart.toLocaleString()}–${rangeEnd.toLocaleString()} of ${total.toLocaleString()}` : ' '}
                        </span>
                        <div className="flex items-center gap-2">
                            <button
                                type="button"
                                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                                disabled={loading || offset === 0}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                <ChevronLeft className="w-3.5 h-3.5" /> Prev
                            </button>
                            <button
                                type="button"
                                onClick={() => setOffset(offset + PAGE_SIZE)}
                                disabled={loading || !data?.capped}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                Next <ChevronRight className="w-3.5 h-3.5" />
                            </button>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
