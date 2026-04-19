'use client';

import React, { useState, useRef, useMemo } from 'react';
import { UploadCloud, File, CheckCircle, AlertTriangle, Bug, Terminal, Network, Cpu, Activity, Folder, ShieldCheck } from 'lucide-react';

type AppSummary = {
    name: string;
    fullPath: string;
    partition: string;
    folder?: string | null;
    destination?: string;
    pool?: string;
};

type F5OSHealth = {
    component: string;
    health: string;
    severity: string;
    attribute?: string;
    description: string;
    value?: string;
};

type XmlStatRow = Record<string, string>;
type XmlStatsPayload = {
    summary: Record<string, number>;
    top_virtual_servers: XmlStatRow[];
    top_pools: XmlStatRow[];
    top_pool_members?: XmlStatRow[];
    tmms: XmlStatRow[];
    interfaces: XmlStatRow[];
    cpus: XmlStatRow[];
    active_modules: XmlStatRow[];
    asm_policies: XmlStatRow[];
    top_expiring_certificates?: XmlStatRow[];
};

export default function QKViewPage() {
    const [file, setFile] = useState<File | null>(null);
    const [isDragging, setIsDragging] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [progressMsg, setProgressMsg] = useState<string>('');
    const [analysisResult, setAnalysisResult] = useState<any>(null);
    const [error, setError] = useState<string | null>(null);
    const [activeCmd, setActiveCmd] = useState<string | null>(null);
    const [activePartition, setActivePartition] = useState<string | null>(null);

    const fileInputRef = useRef<HTMLInputElement>(null);

    const isF5OS = analysisResult?.device_info?.product === 'F5OS';
    const f5osCommands: Record<string, string> = analysisResult?.f5os_commands || {};
    const f5osHealth: F5OSHealth[] = analysisResult?.f5os_health || [];
    const xmlStats: XmlStatsPayload | null = analysisResult?.xml_stats || null;
    const apps: AppSummary[] = analysisResult?.apps || [];
    const partitions: string[] = analysisResult?.partitions || [];
    const diagFiles: string[] = analysisResult?.diag_files || [];

    const appsByPartition = useMemo(() => {
        const out: Record<string, AppSummary[]> = {};
        for (const a of apps) {
            (out[a.partition] ||= []).push(a);
        }
        return out;
    }, [apps]);

    const effectivePartition = activePartition ?? partitions[0] ?? null;

    // Sort interfaces for display: real front-panel NICs (1.1, 1.2, …) first,
    // then mgmt, then everything else (internal / HSB). TMOS stat_module.xml
    // emits them in index order, which looks random to humans.
    const sortedInterfaces = useMemo(() => {
        const list = xmlStats?.interfaces ?? [];
        const rank = (name: string): [number, string] => {
            if (/^\d+\.\d+$/.test(name)) return [0, name];
            if (name === 'mgmt') return [1, name];
            if (name) return [2, name];
            return [3, ''];
        };
        return [...list].sort((a, b) => {
            const [ra, na] = rank(a['name'] || '');
            const [rb, nb] = rank(b['name'] || '');
            if (ra !== rb) return ra - rb;
            return na.localeCompare(nb, undefined, { numeric: true });
        });
    }, [xmlStats]);

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    };

    const handleDragLeave = () => {
        setIsDragging(false);
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFileSelect(e.dataTransfer.files[0]);
        }
    };

    const handleFileSelect = (selectedFile: File) => {
        const validExt = selectedFile.name.endsWith('.qkview') || selectedFile.name.endsWith('.tgz') || selectedFile.name.endsWith('.tar.gz') || selectedFile.name.endsWith('.tar');
        if (!validExt) {
            setError('Please upload a valid .qkview, .tgz, .tar.gz, or .tar archive.');
            return;
        }
        setError(null);
        setFile(selectedFile);
    };

    const uploadFile = async () => {
        if (!file) return;

        setIsUploading(true);
        setError(null);
        setProgressMsg('Uploading archive… 0%');

        // XHR, not fetch, because fetch doesn't expose upload progress. The
        // body is the raw file (octet-stream) — the server reads bytes directly
        // and gives us back an NDJSON stream of pipeline progress + result.
        try {
            const finalData = await new Promise<any>((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', '/api/analyze', true);
                xhr.setRequestHeader('Content-Type', 'application/octet-stream');
                xhr.setRequestHeader('X-Filename', file.name);
                xhr.responseType = 'text';

                xhr.upload.onprogress = (e) => {
                    if (e.lengthComputable) {
                        const pct = Math.floor((e.loaded / e.total) * 100);
                        const mb = (e.loaded / (1024 * 1024)).toFixed(1);
                        const total = (e.total / (1024 * 1024)).toFixed(1);
                        setProgressMsg(`Uploading archive… ${pct}% (${mb} / ${total} MB)`);
                    } else {
                        const mb = (e.loaded / (1024 * 1024)).toFixed(1);
                        setProgressMsg(`Uploading archive… ${mb} MB`);
                    }
                };

                xhr.upload.onload = () => {
                    setProgressMsg('Upload complete — waiting for server…');
                };

                // Track NDJSON lines as they arrive in responseText. This
                // fires repeatedly during streaming; each call sees the full
                // accumulated text, so we only parse what's new.
                let parsedUpto = 0;
                let resultPayload: any = null;
                let streamError: string | null = null;

                const parseNewLines = () => {
                    const text = xhr.responseText || '';
                    if (text.length <= parsedUpto) return;
                    const chunk = text.slice(parsedUpto);
                    const lastNl = chunk.lastIndexOf('\n');
                    if (lastNl === -1) return; // no complete line yet
                    const complete = chunk.slice(0, lastNl);
                    parsedUpto += lastNl + 1;
                    for (const rawLine of complete.split('\n')) {
                        const line = rawLine.trim();
                        if (!line) continue;
                        let evt: any;
                        try { evt = JSON.parse(line); } catch { continue; }
                        if (evt.type === 'progress') {
                            setProgressMsg(evt.msg || '');
                        } else if (evt.type === 'result') {
                            resultPayload = evt.data;
                        } else if (evt.type === 'error') {
                            streamError = evt.detail || 'Analysis failed.';
                        }
                    }
                };

                xhr.onprogress = parseNewLines;

                xhr.onload = () => {
                    parseNewLines();
                    if (xhr.status < 200 || xhr.status >= 300) {
                        // Validation errors (400/413/415) come back as JSON, not NDJSON.
                        let detail = `Upload failed with status ${xhr.status}`;
                        try {
                            const errData = JSON.parse(xhr.responseText || '{}');
                            if (errData?.error) detail = errData.error;
                            else if (errData?.detail) detail = errData.detail;
                        } catch { /* keep generic */ }
                        reject(new Error(detail));
                        return;
                    }
                    if (streamError) { reject(new Error(streamError)); return; }
                    if (!resultPayload) { reject(new Error('Analysis stream ended without a result.')); return; }
                    resolve(resultPayload);
                };

                xhr.onerror = () => reject(new Error('Network error during upload.'));
                xhr.onabort = () => reject(new Error('Upload aborted.'));

                xhr.send(file);
            });

            setAnalysisResult(finalData);
        } catch (err: any) {
            setError(err.message || 'An error occurred during analysis.');
        } finally {
            setIsUploading(false);
            setProgressMsg('');
        }
    };

    return (
        <div className="py-8 space-y-8 max-w-5xl mx-auto">
            <div className="space-y-4">
                <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50">
                    QKView Log Analyzer
                </h1>
                <p className="text-slate-600 dark:text-slate-400">
                    Drag and drop an F5 TMOS (.qkview) or F5OS (.tgz / .tar) diagnostic archive to parse logs, index configurations, and scan for known issues.
                </p>
            </div>

            {/* Upload Area */}
            {!analysisResult && (
                <div
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    className={`border-2 border-dashed rounded-xl p-12 text-center transition-all ${isDragging ? 'border-amber-500 bg-amber-50 dark:bg-amber-900/10' : 'border-slate-300 dark:border-slate-700 hover:border-slate-400 dark:hover:border-slate-600'}`}
                >
                    <input
                        type="file"
                        accept=".qkview,.tgz,.tar.gz,.tar"
                        ref={fileInputRef}
                        className="hidden"
                        onChange={(e) => e.target.files && handleFileSelect(e.target.files[0])}
                    />
                    <div className="flex flex-col items-center justify-center space-y-4">
                        <div className="p-4 bg-slate-100 dark:bg-slate-800 rounded-full text-slate-500 dark:text-slate-400">
                            <UploadCloud className="w-8 h-8" />
                        </div>
                        {file ? (
                            <div className="space-y-4">
                                <p className="text-lg font-medium text-slate-700 dark:text-slate-300">
                                    Selected: {file.name}
                                </p>
                                <button
                                    onClick={uploadFile}
                                    disabled={isUploading}
                                    className="px-6 py-2 bg-amber-600 hover:bg-amber-700 text-white font-medium rounded-lg disabled:opacity-50 transition-colors"
                                >
                                    {isUploading ? 'Analyzing Archive...' : 'Begin Analysis'}
                                </button>
                                {isUploading && progressMsg && (
                                    <p className="text-sm text-slate-600 dark:text-slate-400 font-mono">
                                        {progressMsg}
                                    </p>
                                )}
                            </div>
                        ) : (
                            <div className="space-y-2">
                                <p className="text-lg font-medium text-slate-700 dark:text-slate-300">
                                    Click or drag .qkview, .tgz, or .tar archive to this area to upload
                                </p>
                                <button
                                    onClick={() => fileInputRef.current?.click()}
                                    className="text-amber-600 dark:text-amber-400 font-medium hover:underline"
                                >
                                    Browse Files
                                </button>
                            </div>
                        )}
                        {error && <p className="text-red-500 font-medium mt-4">{error}</p>}
                    </div>
                </div>
            )}

            {/* Results Display */}
            {analysisResult && (
                <div className="space-y-8 animate-in fade-in duration-500">
                    <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 flex justify-between items-center">
                        <div>
                            <h2 className="text-xl font-bold flex items-center gap-2">
                                <CheckCircle className="text-green-500 w-6 h-6" /> Analysis Complete
                            </h2>
                            <p className="text-slate-600 dark:text-slate-400 mt-1">Hostname: {analysisResult.device_info?.hostname} | Platform: {analysisResult.device_info?.platform}</p>
                        </div>
                        <button
                            onClick={() => { setFile(null); setAnalysisResult(null); }}
                            className="px-4 py-2 border border-slate-300 dark:border-slate-600 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-700 font-medium transition-colors"
                        >
                            Analyze Another
                        </button>
                    </div>

                    <div className="grid md:grid-cols-2 gap-6">
                        {/* Device Info Card */}
                        <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
                            <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                <File className="w-5 h-5 text-blue-500" /> System Specifications
                            </h3>
                            <ul className="space-y-2 text-sm text-slate-700 dark:text-slate-300">
                                <li><strong>Product:</strong> {analysisResult.device_info?.product || 'Unknown'}</li>
                                <li><strong>Version:</strong> {analysisResult.device_info?.version || 'Unknown'}</li>
                                <li><strong>Build:</strong> {analysisResult.device_info?.build || 'Unknown'}</li>
                                <li><strong>Cores:</strong> {analysisResult.device_info?.cores ? analysisResult.device_info.cores : 'N/A'}</li>
                                <li><strong>Memory:</strong> {analysisResult.device_info?.memory_mb ? `${analysisResult.device_info.memory_mb} MB` : 'Unknown'}</li>
                            </ul>
                        </div>

                        {/* Known Issues Card */}
                        <div className="p-6 flex flex-col bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[500px] overflow-y-auto">
                            <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                <Bug className="w-5 h-5 text-red-500" /> Known Issues Detected
                            </h3>
                            {analysisResult.findings && analysisResult.findings.length > 0 ? (
                                <div className="space-y-4">
                                    {analysisResult.findings.map((finding: any, idx: number) => (
                                        <div key={idx} className="p-4 bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800 rounded-lg text-sm">
                                            <p className="font-bold text-red-800 dark:text-red-300 mb-1">{finding.rule_name} <span className="text-xs font-normal px-2 py-0.5 ml-2 bg-red-200 dark:bg-red-800 rounded">{finding.severity.toUpperCase()}</span></p>
                                            <p className="text-red-700 dark:text-red-400 mb-3">{finding.description}</p>

                                            {finding.sample_entries && finding.sample_entries.length > 0 && (
                                                <div className="mt-2 text-xs font-mono bg-white dark:bg-black/40 border border-red-100 dark:border-red-900/50 rounded p-2 overflow-x-auto">
                                                    <p className="text-slate-500 mb-1 font-sans font-semibold">Matched Log Samples:</p>
                                                    {finding.sample_entries.map((sample: any, sIdx: number) => (
                                                        <div key={sIdx} className="whitespace-pre-wrap text-slate-800 dark:text-slate-300 leading-relaxed mb-1 border-b border-red-100 dark:border-red-900/40 pb-1 last:border-0 last:pb-0">
                                                            <span className="text-slate-400 mr-2">[{sample.timestamp}]</span>
                                                            {sample.raw_line}
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <p className="text-sm text-slate-600 dark:text-slate-400">No known issues detected in the logs.</p>
                            )}
                        </div>
                    </div>

                    {/* F5OS Quick Links + Health (F5OS only) */}
                    {isF5OS && (Object.keys(f5osCommands).length > 0 || f5osHealth.length > 0) && (
                        <div className="grid md:grid-cols-3 gap-6">
                            <div className="md:col-span-1 p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[500px] overflow-y-auto">
                                <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                    <Terminal className="w-5 h-5 text-amber-500" /> iHealth Quick Links
                                </h3>
                                {Object.keys(f5osCommands).length === 0 ? (
                                    <p className="text-sm text-slate-500">No command outputs captured.</p>
                                ) : (
                                    <ul className="space-y-1 text-sm">
                                        {Object.keys(f5osCommands).sort().map((name) => (
                                            <li key={name}>
                                                <button
                                                    onClick={() => setActiveCmd(name === activeCmd ? null : name)}
                                                    className={`w-full text-left px-2 py-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700/50 font-mono text-xs ${name === activeCmd ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300' : 'text-slate-700 dark:text-slate-300'}`}
                                                >
                                                    {name}
                                                </button>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>

                            <div className="md:col-span-2 p-6 bg-slate-900 rounded-xl shadow-lg border border-slate-800 max-h-[500px] overflow-hidden flex flex-col">
                                <h3 className="font-semibold text-lg mb-4 text-slate-200 flex items-center gap-2">
                                    <Terminal className="w-5 h-5 text-amber-400" />
                                    {activeCmd ? activeCmd : 'Select a command on the left'}
                                </h3>
                                <pre className="flex-1 bg-black/40 rounded p-4 text-xs text-green-300 font-mono overflow-auto whitespace-pre-wrap">
                                    {activeCmd ? f5osCommands[activeCmd] : '# no command selected'}
                                </pre>
                            </div>
                        </div>
                    )}

                    {/* F5OS Health entries */}
                    {isF5OS && f5osHealth.length > 0 && (
                        <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
                            <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                <AlertTriangle className="w-5 h-5 text-red-500" /> F5OS Health ({f5osHealth.length})
                            </h3>
                            <ul className="divide-y divide-slate-100 dark:divide-slate-700 text-sm">
                                {f5osHealth.map((h, i) => {
                                    const sev = h.severity || 'info';
                                    const color = sev === 'critical' ? 'text-red-700 dark:text-red-300' : sev === 'error' ? 'text-red-600 dark:text-red-400' : sev === 'warning' ? 'text-amber-600 dark:text-amber-400' : 'text-slate-600 dark:text-slate-400';
                                    return (
                                        <li key={i} className="py-2 flex items-start gap-3">
                                            <span className={`uppercase font-semibold text-xs w-16 shrink-0 ${color}`}>{sev}</span>
                                            <span className="font-mono text-xs text-slate-500 w-32 shrink-0">{h.component}</span>
                                            <span className="text-slate-700 dark:text-slate-300">{h.description}</span>
                                        </li>
                                    );
                                })}
                            </ul>
                        </div>
                    )}

                    {/* Apps Browser (TMOS only) */}
                    {!isF5OS && apps.length > 0 && (
                        <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
                            <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                                <Folder className="w-5 h-5 text-blue-500" /> Configured Virtual Servers ({apps.length})
                            </h3>
                            <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
                                From bigip.conf — user-defined apps only. Runtime stats below may show more (system/internal VS).
                            </p>
                            {partitions.length > 1 && (
                                <div className="flex flex-wrap gap-2 mb-4">
                                    {partitions.map((p) => (
                                        <button
                                            key={p}
                                            onClick={() => setActivePartition(p)}
                                            className={`px-3 py-1 rounded text-xs font-medium ${p === effectivePartition ? 'bg-amber-600 text-white' : 'bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600'}`}
                                        >
                                            {p}
                                        </button>
                                    ))}
                                </div>
                            )}
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead className="text-xs text-slate-500 uppercase tracking-wider border-b border-slate-200 dark:border-slate-700">
                                        <tr>
                                            <th className="text-left py-2 pr-4 font-semibold">Name</th>
                                            <th className="text-left py-2 pr-4 font-semibold">Destination</th>
                                            <th className="text-left py-2 font-semibold">Pool</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                        {(effectivePartition
                                            ? (appsByPartition[effectivePartition] || [])
                                            : apps
                                        ).map((a) => (
                                            <tr key={a.fullPath} className="hover:bg-slate-50 dark:hover:bg-slate-700/40">
                                                <td className="py-2 pr-4 font-mono text-xs text-slate-800 dark:text-slate-200">{a.name}</td>
                                                <td className="py-2 pr-4 font-mono text-xs text-slate-600 dark:text-slate-400">{a.destination || '—'}</td>
                                                <td className="py-2 font-mono text-xs text-slate-600 dark:text-slate-400">{a.pool || '—'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {/* XML Stats panels (TMOS only) */}
                    {xmlStats && (
                        <div className="grid md:grid-cols-2 gap-6">
                            <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
                                <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                    <Activity className="w-5 h-5 text-emerald-500" /> Runtime Stats
                                </h3>
                                <ul className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                                    {Object.entries(xmlStats.summary).map(([k, v]) => (
                                        <li key={k} className="flex justify-between border-b border-slate-100 dark:border-slate-700 pb-1">
                                            <span className="text-slate-500 font-mono text-xs">{k}</span>
                                            <span className="text-slate-800 dark:text-slate-200 font-semibold">{v}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>

                            <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[400px] overflow-y-auto">
                                <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                    <Network className="w-5 h-5 text-indigo-500" /> Top Virtual Servers
                                </h3>
                                {xmlStats.top_virtual_servers.length === 0 ? (
                                    <p className="text-sm text-slate-500">No VS stats in archive.</p>
                                ) : (
                                    <table className="w-full text-xs">
                                        <thead className="text-slate-500 uppercase">
                                            <tr>
                                                <th className="text-left py-1 pr-2">Name</th>
                                                <th className="text-right py-1 pr-2">Cur Conns</th>
                                                <th className="text-right py-1">Tot Conns</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                            {xmlStats.top_virtual_servers.map((vs, i) => (
                                                <tr key={i}>
                                                    <td className="py-1 pr-2 font-mono">{vs['name'] || vs['vs_name'] || '—'}</td>
                                                    <td className="py-1 pr-2 text-right tabular-nums">{vs['clientside.cur_conns'] || '0'}</td>
                                                    <td className="py-1 text-right tabular-nums">{vs['clientside.tot_conns'] || '0'}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                            </div>

                            {xmlStats.tmms.length > 0 && (
                                <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[400px] overflow-y-auto">
                                    <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                        <Cpu className="w-5 h-5 text-purple-500" /> TMM CPU ({xmlStats.tmms.length})
                                    </h3>
                                    <table className="w-full text-xs">
                                        <thead className="text-slate-500 uppercase">
                                            <tr>
                                                <th className="text-left py-1 pr-2">TMM</th>
                                                <th className="text-right py-1 pr-2">1s</th>
                                                <th className="text-right py-1 pr-2">1m</th>
                                                <th className="text-right py-1 pr-2">5m</th>
                                                <th className="text-right py-1">Conns</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                            {xmlStats.tmms.map((t, i) => {
                                                const oneMin = parseInt(t['cpu_usage_1min'] || '0', 10);
                                                const color = oneMin >= 80
                                                    ? 'text-red-600 dark:text-red-400'
                                                    : oneMin >= 60
                                                        ? 'text-amber-600 dark:text-amber-400'
                                                        : 'text-slate-700 dark:text-slate-300';
                                                return (
                                                    <tr key={i} className={color}>
                                                        <td className="py-1 pr-2 font-mono">cpu {t['cpu']}/slot {t['slot_id']}</td>
                                                        <td className="py-1 pr-2 text-right tabular-nums">{t['cpu_usage_1sec'] || '—'}</td>
                                                        <td className="py-1 pr-2 text-right tabular-nums font-semibold">{t['cpu_usage_1min'] || '—'}</td>
                                                        <td className="py-1 pr-2 text-right tabular-nums">{t['cpu_usage_5mins'] || '—'}</td>
                                                        <td className="py-1 text-right tabular-nums">{t['client_side_traffic.cur_conns'] || '0'}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {xmlStats.cpus.length > 0 && (
                                <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[400px] overflow-y-auto">
                                    <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                        <Cpu className="w-5 h-5 text-fuchsia-500" /> System CPU ({xmlStats.cpus.length})
                                    </h3>
                                    <table className="w-full text-xs">
                                        <thead className="text-slate-500 uppercase">
                                            <tr>
                                                <th className="text-left py-1 pr-2">CPU</th>
                                                <th className="text-left py-1 pr-2">Plane</th>
                                                <th className="text-right py-1 pr-2">5s</th>
                                                <th className="text-right py-1 pr-2">1m</th>
                                                <th className="text-right py-1">5m</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                            {xmlStats.cpus.map((c, i) => {
                                                const oneMin = parseInt(c['one_min_avg.ratio'] || '0', 10);
                                                const color = oneMin >= 80
                                                    ? 'text-red-600 dark:text-red-400'
                                                    : oneMin >= 60
                                                        ? 'text-amber-600 dark:text-amber-400'
                                                        : 'text-slate-700 dark:text-slate-300';
                                                return (
                                                    <tr key={i} className={color}>
                                                        <td className="py-1 pr-2 font-mono">cpu {c['cpu_id']}/slot {c['slot_id']}</td>
                                                        <td className="py-1 pr-2 text-slate-500">{c['plane_name'] || '—'}</td>
                                                        <td className="py-1 pr-2 text-right tabular-nums">{c['five_sec_avg.ratio'] || '—'}</td>
                                                        <td className="py-1 pr-2 text-right tabular-nums font-semibold">{c['one_min_avg.ratio'] || '—'}</td>
                                                        <td className="py-1 text-right tabular-nums">{c['five_min_avg.ratio'] || '—'}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {xmlStats.interfaces.length > 0 && (
                                <div className="md:col-span-2 p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[400px] overflow-y-auto">
                                    <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                                        <Network className="w-5 h-5 text-cyan-500" /> Interfaces ({xmlStats.interfaces.length})
                                    </h3>
                                    <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
                                        Rows with any non-zero error, drop, or collision are highlighted.
                                    </p>
                                    <table className="w-full text-xs">
                                        <thead className="text-slate-500 uppercase">
                                            <tr>
                                                <th className="text-left py-1 pr-2">Name</th>
                                                <th className="text-right py-1 pr-2">Pkts In</th>
                                                <th className="text-right py-1 pr-2">Pkts Out</th>
                                                <th className="text-right py-1 pr-2">Err In</th>
                                                <th className="text-right py-1 pr-2">Err Out</th>
                                                <th className="text-right py-1 pr-2">Drop In</th>
                                                <th className="text-right py-1 pr-2">Drop Out</th>
                                                <th className="text-right py-1">Coll</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                            {sortedInterfaces.map((intf, i) => {
                                                const errIn = parseInt(intf['counters.errors_in'] || '0', 10);
                                                const errOut = parseInt(intf['counters.errors_out'] || '0', 10);
                                                const dropIn = parseInt(intf['counters.drops_in'] || '0', 10);
                                                const dropOut = parseInt(intf['counters.drops_out'] || '0', 10);
                                                const coll = parseInt(intf['counters.collisions'] || '0', 10);
                                                const hasTrouble = errIn + errOut + dropIn + dropOut + coll > 0;
                                                const rowColor = hasTrouble
                                                    ? 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30'
                                                    : 'text-slate-700 dark:text-slate-300';
                                                const troubleCell = (v: number) =>
                                                    v > 0 ? 'font-semibold' : 'text-slate-400 dark:text-slate-600';
                                                return (
                                                    <tr key={i} className={rowColor}>
                                                        <td className="py-1 pr-2 font-mono">{intf['name'] || `idx ${intf['if_index']}`}</td>
                                                        <td className="py-1 pr-2 text-right tabular-nums">{intf['counters.pkts_in'] || '0'}</td>
                                                        <td className="py-1 pr-2 text-right tabular-nums">{intf['counters.pkts_out'] || '0'}</td>
                                                        <td className={`py-1 pr-2 text-right tabular-nums ${troubleCell(errIn)}`}>{errIn}</td>
                                                        <td className={`py-1 pr-2 text-right tabular-nums ${troubleCell(errOut)}`}>{errOut}</td>
                                                        <td className={`py-1 pr-2 text-right tabular-nums ${troubleCell(dropIn)}`}>{dropIn}</td>
                                                        <td className={`py-1 pr-2 text-right tabular-nums ${troubleCell(dropOut)}`}>{dropOut}</td>
                                                        <td className={`py-1 text-right tabular-nums ${troubleCell(coll)}`}>{coll}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {xmlStats.top_pools && xmlStats.top_pools.length > 0 && (
                                <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[400px] overflow-y-auto">
                                    <h3 className="font-semibold text-lg mb-4 flex items-center gap-2">
                                        <Network className="w-5 h-5 text-orange-500" /> Top Pools
                                    </h3>
                                    <table className="w-full text-xs">
                                        <thead className="text-slate-500 uppercase">
                                            <tr>
                                                <th className="text-left py-1 pr-2">Name</th>
                                                <th className="text-right py-1 pr-2">Cur</th>
                                                <th className="text-right py-1">Total</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                            {xmlStats.top_pools.map((p, i) => (
                                                <tr key={i}>
                                                    <td className="py-1 pr-2 font-mono">{p['name'] || '—'}</td>
                                                    <td className="py-1 pr-2 text-right tabular-nums">{p['serverside.cur_conns'] || '0'}</td>
                                                    <td className="py-1 text-right tabular-nums">{p['serverside.tot_conns'] || '0'}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {xmlStats.top_pool_members && xmlStats.top_pool_members.length > 0 && (
                                <div className="md:col-span-2 p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[500px] overflow-y-auto">
                                    <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                                        <Activity className="w-5 h-5 text-teal-500" /> Top Pool Members ({xmlStats.top_pool_members.length})
                                    </h3>
                                    <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
                                        Ordered by serverside total connections. Member up/down state lives in config — this is the traffic-volume view.
                                    </p>
                                    <table className="w-full text-xs">
                                        <thead className="text-slate-500 uppercase">
                                            <tr>
                                                <th className="text-left py-1 pr-2">Member</th>
                                                <th className="text-right py-1 pr-2">Port</th>
                                                <th className="text-right py-1 pr-2">Cur Conns</th>
                                                <th className="text-right py-1 pr-2">Tot Conns</th>
                                                <th className="text-right py-1 pr-2">Requests</th>
                                                <th className="text-right py-1">Bytes In</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                            {xmlStats.top_pool_members.map((m, i) => (
                                                <tr key={i}>
                                                    <td className="py-1 pr-2 font-mono truncate max-w-[20rem]" title={m['name']}>{m['name'] || '—'}</td>
                                                    <td className="py-1 pr-2 text-right tabular-nums font-mono">{m['port'] || '—'}</td>
                                                    <td className="py-1 pr-2 text-right tabular-nums">{m['serverside.cur_conns'] || '0'}</td>
                                                    <td className="py-1 pr-2 text-right tabular-nums font-semibold">{m['serverside.tot_conns'] || '0'}</td>
                                                    <td className="py-1 pr-2 text-right tabular-nums">{m['tot_requests'] || '0'}</td>
                                                    <td className="py-1 text-right tabular-nums">{m['serverside.bytes_in'] || '0'}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {xmlStats.top_expiring_certificates && xmlStats.top_expiring_certificates.length > 0 && (
                                <div className="md:col-span-2 p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700 max-h-[500px] overflow-y-auto">
                                    <h3 className="font-semibold text-lg mb-1 flex items-center gap-2">
                                        <ShieldCheck className="w-5 h-5 text-rose-500" /> Certificate Expiry ({xmlStats.top_expiring_certificates.length})
                                    </h3>
                                    <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
                                        Soonest-to-expire first. Red row: &lt;30 days. Amber: &lt;90 days.
                                    </p>
                                    <table className="w-full text-xs">
                                        <thead className="text-slate-500 uppercase">
                                            <tr>
                                                <th className="text-left py-1 pr-2">Name</th>
                                                <th className="text-left py-1 pr-2">Subject</th>
                                                <th className="text-left py-1 pr-2">Issuer</th>
                                                <th className="text-left py-1 pr-2">Expires</th>
                                                <th className="text-right py-1">Days</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                                            {xmlStats.top_expiring_certificates.map((c, i) => {
                                                const epoch = parseInt(c['expiration_date'] || '0', 10);
                                                const days = epoch > 0
                                                    ? Math.floor((epoch * 1000 - Date.now()) / 86400000)
                                                    : null;
                                                const rowColor = days === null
                                                    ? 'text-slate-700 dark:text-slate-300'
                                                    : days < 30
                                                        ? 'text-red-600 dark:text-red-400'
                                                        : days < 90
                                                            ? 'text-amber-600 dark:text-amber-400'
                                                            : 'text-slate-700 dark:text-slate-300';
                                                const expires = c['expiration_string']
                                                    || (epoch > 0 ? new Date(epoch * 1000).toISOString().slice(0, 10) : '—');
                                                return (
                                                    <tr key={i} className={rowColor}>
                                                        <td className="py-1 pr-2 font-mono truncate max-w-[14rem]" title={c['name']}>{c['name'] || '—'}</td>
                                                        <td className="py-1 pr-2 truncate max-w-[18rem]" title={c['subject']}>{c['subject'] || '—'}</td>
                                                        <td className="py-1 pr-2 truncate max-w-[18rem]" title={c['issuer']}>{c['issuer'] || '—'}</td>
                                                        <td className="py-1 pr-2 font-mono whitespace-nowrap">{expires}</td>
                                                        <td className="py-1 text-right tabular-nums font-semibold">{days ?? '—'}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Diag files list (TMOS) */}
                    {diagFiles.length > 0 && (
                        <div className="p-6 bg-white dark:bg-slate-800 rounded-xl shadow-sm border border-slate-200 dark:border-slate-700">
                            <h3 className="font-semibold text-lg mb-3 flex items-center gap-2">
                                <File className="w-5 h-5 text-slate-500" /> Diag Dumps ({diagFiles.length})
                            </h3>
                            <ul className="flex flex-wrap gap-2 text-xs font-mono">
                                {diagFiles.map((n) => (
                                    <li key={n} className="px-2 py-1 bg-slate-100 dark:bg-slate-700 rounded">{n}</li>
                                ))}
                            </ul>
                        </div>
                    )}

                    {/* Terminal Window for Raw Logs */}
                    <div className="p-6 bg-slate-900 rounded-xl shadow-lg border border-slate-800">
                        <div className="flex items-center justify-between mb-4 border-b border-slate-800 pb-4">
                            <h3 className="font-semibold text-lg text-slate-200 flex items-center gap-2">
                                <div className="flex gap-1.5 mr-2">
                                    <div className="w-3 h-3 rounded-full bg-red-500"></div>
                                    <div className="w-3 h-3 rounded-full bg-amber-500"></div>
                                    <div className="w-3 h-3 rounded-full bg-green-500"></div>
                                </div>
                                Extracted Critical/Warning Logs ({analysisResult.entry_count || 0})
                            </h3>
                        </div>
                        <div className="bg-black/50 rounded border border-slate-800 p-4 h-96 overflow-y-auto font-mono text-sm">
                            {analysisResult.entries && analysisResult.entries.length > 0 ? (
                                <ul className="space-y-1">
                                    {analysisResult.entries.slice(0, 150).map((entry: any, i: number) => {
                                        const sevColor = entry.severity === 'err' || entry.severity === 'critical' ? 'text-red-400'
                                            : entry.severity === 'warning' ? 'text-amber-400'
                                                : entry.severity === 'notice' ? 'text-blue-400'
                                                    : 'text-slate-300';
                                        return (
                                            <li key={i} className={`whitespace-pre-wrap leading-relaxed border-b border-slate-800/50 pb-1 ${sevColor}`}>
                                                <span className="text-slate-500 mr-2">{entry.timestamp}</span>
                                                <span className="opacity-75 mr-2">[{entry.process}]</span>
                                                {entry.raw_line}
                                            </li>
                                        );
                                    })}
                                    {analysisResult.entries.length > 150 && (
                                        <li className="text-slate-500 pt-2 italic">... {analysisResult.entries.length - 150} more entries truncated from view ...</li>
                                    )}
                                </ul>
                            ) : (
                                <p className="text-slate-500 italic">No significant warning/error logs found in archive.</p>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
