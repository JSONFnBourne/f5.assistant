'use client'

import React, { useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Sparkles, Plus, Trash2, AlertCircle, Copy, Check } from 'lucide-react'

// ── Protocol → valid events ───────────────────────────────────────────────────

type Protocol = 'HTTP' | 'HTTPS (ClientSSL)' | 'TCP' | 'UDP' | 'DNS' | 'SSL (ServerSSL)' | 'SIP' | 'WebSocket' | 'ASM'

const PROTOCOL_EVENTS: Record<Protocol, string[]> = {
    'HTTP': [
        'RULE_INIT', 'HTTP_REQUEST', 'HTTP_RESPONSE',
        'HTTP_REQUEST_DATA', 'HTTP_RESPONSE_DATA',
        'HTTP_REQUEST_SEND', 'HTTP_REQUEST_RELEASE',
        'HTTP_RESPONSE_RELEASE', 'HTTP_RESPONSE_CONTINUE',
        'HTTP_REJECT', 'LB_SELECTED', 'LB_FAILED',
    ],
    'HTTPS (ClientSSL)': [
        'RULE_INIT', 'CLIENTSSL_HANDSHAKE', 'CLIENTSSL_CLIENTHELLO',
        'CLIENTSSL_SERVERHELLO_SEND', 'CLIENTSSL_CLIENTCERT', 'CLIENTSSL_DATA',
        'HTTP_REQUEST', 'HTTP_RESPONSE',
    ],
    'TCP': [
        'RULE_INIT', 'CLIENT_ACCEPTED', 'CLIENT_DATA', 'CLIENT_CLOSED',
        'SERVER_CONNECTED', 'SERVER_DATA', 'SERVER_CLOSED',
        'SERVER_INIT', 'FLOW_INIT', 'LB_SELECTED', 'LB_FAILED',
    ],
    'UDP': [
        'RULE_INIT', 'CLIENT_ACCEPTED', 'CLIENT_DATA',
        'CLIENT_CLOSED', 'SERVER_DATA',
    ],
    'DNS': ['RULE_INIT', 'DNS_REQUEST', 'DNS_RESPONSE'],
    'SSL (ServerSSL)': [
        'RULE_INIT', 'SERVERSSL_HANDSHAKE', 'SERVERSSL_CLIENTHELLO_SEND',
        'SERVERSSL_SERVERCERT', 'SERVERSSL_SERVERHELLO', 'SERVERSSL_DATA',
    ],
    'SIP': [
        'RULE_INIT', 'SIP_REQUEST', 'SIP_RESPONSE',
        'SIP_REQUEST_SEND', 'SIP_RESPONSE_SEND',
    ],
    'WebSocket': [
        'RULE_INIT', 'WS_REQUEST', 'WS_RESPONSE',
        'WS_CLIENT_FRAME', 'WS_SERVER_FRAME',
        'WS_CLIENT_DATA', 'WS_SERVER_DATA',
    ],
    'ASM': [
        'RULE_INIT', 'ASM_REQUEST_DONE', 'ASM_REQUEST_VIOLATION',
        'ASM_RESPONSE_VIOLATION', 'ASM_REQUEST_BLOCKING',
    ],
}

// ── Event → condition commands ────────────────────────────────────────────────

const EVENT_CONDITION_CMDS: Record<string, string[]> = {
    HTTP_REQUEST: ['HTTP::uri', 'HTTP::host', 'HTTP::method', 'HTTP::header', 'HTTP::cookie', 'HTTP::version', 'IP::client_addr', 'IP::addr', 'TCP::local_port'],
    HTTP_RESPONSE: ['HTTP::status', 'HTTP::header', 'HTTP::content_type'],
    HTTP_REQUEST_DATA: ['HTTP::payload', 'HTTP::content'],
    HTTP_RESPONSE_DATA: ['HTTP::payload', 'HTTP::content'],
    HTTP_REQUEST_SEND: ['HTTP::uri', 'HTTP::host', 'HTTP::header', 'HTTP::method'],
    HTTP_REQUEST_RELEASE: ['HTTP::uri', 'HTTP::header'],
    HTTP_RESPONSE_RELEASE: ['HTTP::status', 'HTTP::header'],
    HTTP_RESPONSE_CONTINUE: ['HTTP::status'],
    CLIENT_ACCEPTED: ['IP::client_addr', 'IP::addr', 'TCP::local_port'],
    CLIENT_DATA: ['TCP::payload'],
    SERVER_CONNECTED: ['IP::server_addr', 'TCP::server_port'],
    SERVER_DATA: ['TCP::payload'],
    CLIENTSSL_HANDSHAKE: ['SSL::cipher', 'SSL::cert', 'SSL::sessionid'],
    CLIENTSSL_CLIENTHELLO: ['SSL::extensions', 'SSL::cipher_list'],
    CLIENTSSL_SERVERHELLO_SEND: ['SSL::cipher'],
    SERVERSSL_HANDSHAKE: ['SSL::cipher', 'SSL::cert', 'SSL::sessionid'],
    SERVERSSL_SERVERCERT: ['SSL::cert'],
    SERVERSSL_SERVERHELLO: ['SSL::cipher'],
    DNS_REQUEST: ['DNS::name', 'DNS::type', 'DNS::class'],
    DNS_RESPONSE: ['DNS::name', 'DNS::type', 'DNS::rcode'],
    SIP_REQUEST: ['SIP::uri', 'SIP::method', 'SIP::header', 'SIP::from', 'SIP::to'],
    SIP_RESPONSE: ['SIP::status', 'SIP::header', 'SIP::from', 'SIP::to'],
    WS_REQUEST: ['HTTP::uri', 'HTTP::header'],
    WS_CLIENT_FRAME: ['WS::frame_length', 'WS::frame_is_masked'],
    WS_SERVER_FRAME: ['WS::frame_length'],
    LB_SELECTED: ['LB::server', 'LB::pool'],
    LB_FAILED: ['LB::pool'],
    FLOW_INIT: ['IP::client_addr', 'IP::addr', 'TCP::local_port'],
    SERVER_INIT: ['IP::server_addr'],
    ASM_REQUEST_DONE: ['ASM::status', 'ASM::violation', 'ASM::support_id'],
    ASM_REQUEST_VIOLATION: ['ASM::violation', 'ASM::severity', 'ASM::support_id'],
    ASM_RESPONSE_VIOLATION: ['ASM::violation', 'ASM::severity'],
    ASM_REQUEST_BLOCKING: ['ASM::violation', 'ASM::support_id'],
}

// ── Event → action commands ───────────────────────────────────────────────────

const EVENT_ACTION_CMDS: Record<string, string[]> = {
    HTTP_REQUEST: ['pool', 'node', 'HTTP::redirect', 'HTTP::respond', 'HTTP::header insert', 'HTTP::header remove', 'HTTP::header replace', 'HTTP::uri', 'HTTP::collect', 'log', 'reject', 'drop'],
    HTTP_RESPONSE: ['HTTP::header insert', 'HTTP::header remove', 'HTTP::header replace', 'HTTP::respond', 'HTTP::collect', 'log'],
    HTTP_REQUEST_DATA: ['HTTP::payload replace', 'HTTP::release', 'log', 'reject'],
    HTTP_RESPONSE_DATA: ['HTTP::payload replace', 'HTTP::release', 'log'],
    HTTP_REQUEST_SEND: ['HTTP::header insert', 'HTTP::header remove', 'HTTP::uri', 'log'],
    HTTP_REQUEST_RELEASE: ['HTTP::header insert', 'HTTP::header remove', 'log'],
    HTTP_RESPONSE_RELEASE: ['HTTP::header insert', 'HTTP::header remove', 'log'],
    HTTP_RESPONSE_CONTINUE: ['log'],
    HTTP_REJECT: ['log'],
    CLIENT_ACCEPTED: ['pool', 'node', 'reject', 'drop', 'TCP::collect', 'log'],
    CLIENT_DATA: ['TCP::payload replace', 'TCP::release', 'reject', 'log'],
    CLIENT_CLOSED: ['log'],
    SERVER_CONNECTED: ['node', 'log'],
    SERVER_DATA: ['TCP::payload replace', 'TCP::release', 'log'],
    SERVER_CLOSED: ['log'],
    SERVER_INIT: ['pool', 'node', 'log'],
    FLOW_INIT: ['pool', 'reject', 'log'],
    CLIENTSSL_HANDSHAKE: ['SSL::disable', 'SSL::enable', 'reject', 'log'],
    CLIENTSSL_CLIENTHELLO: ['SSL::disable', 'reject', 'log'],
    CLIENTSSL_SERVERHELLO_SEND: ['log'],
    CLIENTSSL_CLIENTCERT: ['SSL::cert', 'reject', 'log'],
    CLIENTSSL_DATA: ['SSL::collect', 'SSL::release', 'log'],
    SERVERSSL_HANDSHAKE: ['SSL::disable', 'SSL::enable', 'reject', 'log'],
    SERVERSSL_SERVERCERT: ['reject', 'log'],
    SERVERSSL_DATA: ['SSL::collect', 'SSL::release', 'log'],
    DNS_REQUEST: ['DNS::answer', 'DNS::return', 'reject', 'log'],
    DNS_RESPONSE: ['DNS::answer', 'log'],
    SIP_REQUEST: ['SIP::header insert', 'SIP::header remove', 'reject', 'log'],
    SIP_RESPONSE: ['SIP::header insert', 'SIP::header remove', 'log'],
    SIP_REQUEST_SEND: ['SIP::header insert', 'log'],
    SIP_RESPONSE_SEND: ['SIP::header insert', 'log'],
    WS_REQUEST: ['HTTP::respond', 'reject', 'log'],
    WS_CLIENT_FRAME: ['WS::release', 'reject', 'log'],
    WS_SERVER_FRAME: ['WS::release', 'log'],
    WS_CLIENT_DATA: ['WS::release', 'log'],
    WS_SERVER_DATA: ['WS::release', 'log'],
    LB_SELECTED: ['pool', 'node', 'log'],
    LB_FAILED: ['pool', 'reject', 'log'],
    ASM_REQUEST_DONE: ['ASM::unblock', 'log'],
    ASM_REQUEST_BLOCKING: ['ASM::unblock', 'HTTP::respond', 'log'],
    ASM_REQUEST_VIOLATION: ['log'],
    ASM_RESPONSE_VIOLATION: ['log'],
}

const OPERATORS = ['eq', 'ne', 'starts_with', 'ends_with', 'contains', 'matches', 'matches_glob', 'matches_regex']

// ── Types ─────────────────────────────────────────────────────────────────────

type Condition = { id: string; command: string; operator: string; value: string }
type ActionItem = { id: string; command: string; value: string }
type EventBlock = { id: string; event: string; conditions: Condition[]; actions: ActionItem[] }

function uid() { return Math.random().toString(36).slice(2, 9) }

// ── Live code builder ─────────────────────────────────────────────────────────

function buildCode(tmosVersion: string, protocol: string, blocks: EventBlock[], dependencies: string): string {
    const date = new Date().toISOString().split('T')[0]
    const profileHint = protocol.toLowerCase().includes('http') ? 'http'
        : protocol.toLowerCase().includes('ssl') ? 'clientssl'
        : protocol.toLowerCase().includes('dns') ? 'dns'
        : protocol.toLowerCase().includes('sip') ? 'sip'
        : 'tcp'

    const header = [
        `# Generated iRule`,
        `# TMOS: ${tmosVersion}  Protocol: ${protocol}`,
        `# Required profile: ${profileHint}`,
        `# Generated: ${date}`,
        dependencies ? `# Notes: ${dependencies}` : '',
        '',
    ].filter(l => l !== null && l !== undefined) as string[]

    const body: string[] = []

    for (const block of blocks) {
        if (block.event === 'RULE_INIT') {
            body.push(`when RULE_INIT {`)
            body.push(`    set static::debug 0`)
            body.push(`}`)
            body.push(``)
            continue
        }

        body.push(`when ${block.event} {`)

        const filledConds = block.conditions.filter(c => c.command && c.value)
        const filledActs = block.actions.filter(a => a.command)

        if (filledConds.length > 0) {
            const condExpr = filledConds
                .map(c => `[${c.command}] ${c.operator} "${c.value}"`)
                .join(' && ')
            body.push(`    if { ${condExpr} } {`)
            for (const a of filledActs) {
                body.push(`        ${a.command}${a.value ? ` ${a.value}` : ''}`)
            }
            if (filledActs.length === 0) {
                body.push(`        # TODO: add actions`)
            }
            body.push(`    }`)
        } else {
            for (const a of filledActs) {
                body.push(`    ${a.command}${a.value ? ` ${a.value}` : ''}`)
            }
            if (filledActs.length === 0) {
                body.push(`    # TODO: add conditions / actions`)
            }
        }

        body.push(`}`)
        body.push(``)
    }

    return [...header, ...body].join('\n').trimEnd() + '\n'
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function GeneratorPage() {
    const [tmosVersion, setTmosVersion] = useState('17.x')
    const [protocol, setProtocol] = useState<Protocol>('HTTP')
    const [blocks, setBlocks] = useState<EventBlock[]>([])
    const [dependencies, setDependencies] = useState('')
    const [generating, setGenerating] = useState(false)
    const [enhancedCode, setEnhancedCode] = useState<string | null>(null)
    const [copied, setCopied] = useState(false)
    const [aiError, setAiError] = useState<string | null>(null)

    const validEvents = PROTOCOL_EVENTS[protocol] ?? []
    const usedEvents = new Set(blocks.map(b => b.event))
    const availableEvents = validEvents.filter(ev => !usedEvents.has(ev))

    const liveCode = buildCode(tmosVersion, protocol, blocks, dependencies)
    const displayCode = enhancedCode ?? liveCode
    const hasBlocks = blocks.length > 0

    // ── Block mutations ────────────────────────────────────────────────────────

    function addEvent(event: string) {
        setBlocks(prev => [...prev, { id: uid(), event, conditions: [], actions: [] }])
        setEnhancedCode(null)
        setAiError(null)
    }

    function removeBlock(id: string) {
        setBlocks(prev => prev.filter(b => b.id !== id))
        setEnhancedCode(null)
    }

    function addCondition(blockId: string) {
        setBlocks(prev => prev.map(b => b.id === blockId
            ? { ...b, conditions: [...b.conditions, { id: uid(), command: '', operator: 'eq', value: '' }] }
            : b))
        setEnhancedCode(null)
    }

    function updateCondition(blockId: string, condId: string, field: keyof Condition, val: string) {
        setBlocks(prev => prev.map(b => b.id === blockId
            ? { ...b, conditions: b.conditions.map(c => c.id === condId ? { ...c, [field]: val } : c) }
            : b))
        setEnhancedCode(null)
    }

    function removeCondition(blockId: string, condId: string) {
        setBlocks(prev => prev.map(b => b.id === blockId
            ? { ...b, conditions: b.conditions.filter(c => c.id !== condId) }
            : b))
        setEnhancedCode(null)
    }

    function addAction(blockId: string) {
        setBlocks(prev => prev.map(b => b.id === blockId
            ? { ...b, actions: [...b.actions, { id: uid(), command: '', value: '' }] }
            : b))
        setEnhancedCode(null)
    }

    function updateAction(blockId: string, actId: string, field: keyof ActionItem, val: string) {
        setBlocks(prev => prev.map(b => b.id === blockId
            ? { ...b, actions: b.actions.map(a => a.id === actId ? { ...a, [field]: val } : a) }
            : b))
        setEnhancedCode(null)
    }

    function removeAction(blockId: string, actId: string) {
        setBlocks(prev => prev.map(b => b.id === blockId
            ? { ...b, actions: b.actions.filter(a => a.id !== actId) }
            : b))
        setEnhancedCode(null)
    }

    // ── AI Enhancement ─────────────────────────────────────────────────────────

    async function handleEnhance() {
        setGenerating(true)
        setAiError(null)
        setEnhancedCode(null)
        try {
            const res = await fetch('/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ skeleton: liveCode, tmosVersion, protocol, dependencies })
            })
            const data = await res.json()
            if (!res.ok || data.error) {
                setAiError(data.error ?? 'Unknown error from AI backend.')
            } else {
                setEnhancedCode(data.code ?? liveCode)
            }
        } catch (e) {
            setAiError('Could not reach /api/generate. Is the server running?')
        } finally {
            setGenerating(false)
        }
    }

    function handleCopy() {
        navigator.clipboard.writeText(displayCode)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
    }

    // ── Shared input styles ────────────────────────────────────────────────────

    const sel = "rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 p-1.5 text-xs text-slate-900 dark:text-slate-100 focus:ring-1 focus:ring-amber-500 outline-none"

    return (
        <div className="max-w-7xl mx-auto space-y-6">
            <div>
                <Link href="/" className="inline-flex items-center text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200 mb-4 transition-colors">
                    <ArrowLeft className="h-4 w-4 mr-1" /> Back to dashboard
                </Link>
                <h1 className="text-3xl font-bold text-slate-900 dark:text-slate-100 flex items-center gap-3">
                    <Sparkles className="h-8 w-8 text-amber-500" />
                    iRule Generator
                </h1>
                <p className="text-slate-600 dark:text-slate-400 mt-2">
                    Pick a protocol, add event blocks, build conditions and actions — code assembles live on the right.
                </p>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 items-start">

                {/* ── Left: Builder ─────────────────────────────────────────── */}
                <div className="space-y-4">

                    {/* Version + Protocol */}
                    <div className="bg-white dark:bg-slate-800 p-4 rounded-xl border dark:border-slate-700 shadow-sm">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-slate-900 dark:text-slate-200">TMOS Version</label>
                                <select
                                    className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 p-2 text-sm text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-amber-500 outline-none"
                                    value={tmosVersion}
                                    onChange={e => setTmosVersion(e.target.value)}
                                >
                                    <option>17.x</option>
                                    <option>16.x</option>
                                    <option>15.x</option>
                                    <option>14.x</option>
                                </select>
                            </div>
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-slate-900 dark:text-slate-200">Protocol</label>
                                <select
                                    className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 p-2 text-sm text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-amber-500 outline-none"
                                    value={protocol}
                                    onChange={e => {
                                        setProtocol(e.target.value as Protocol)
                                        setBlocks([])
                                        setEnhancedCode(null)
                                        setAiError(null)
                                    }}
                                >
                                    {Object.keys(PROTOCOL_EVENTS).map(p => <option key={p}>{p}</option>)}
                                </select>
                            </div>
                        </div>
                    </div>

                    {/* Event blocks */}
                    {blocks.map(block => {
                        const condCmds = EVENT_CONDITION_CMDS[block.event] ?? []
                        const actCmds = EVENT_ACTION_CMDS[block.event] ?? []
                        const isRuleInit = block.event === 'RULE_INIT'

                        return (
                            <div key={block.id} className="bg-white dark:bg-slate-800 rounded-xl border dark:border-slate-700 shadow-sm overflow-hidden">
                                {/* Header */}
                                <div className="flex items-center justify-between px-4 py-2.5 bg-slate-100 dark:bg-slate-700/60 border-b dark:border-slate-700">
                                    <span className="font-mono text-sm font-semibold text-amber-700 dark:text-amber-400">
                                        when {block.event}
                                    </span>
                                    <button onClick={() => removeBlock(block.id)} className="text-slate-400 hover:text-red-500 transition-colors" title="Remove block">
                                        <Trash2 className="h-4 w-4" />
                                    </button>
                                </div>

                                {isRuleInit ? (
                                    <div className="px-4 py-3 text-sm text-slate-500 dark:text-slate-400">
                                        Auto-inserts{' '}
                                        <code className="font-mono text-xs bg-slate-100 dark:bg-slate-700 px-1.5 py-0.5 rounded">
                                            set static::debug 0
                                        </code>
                                        {' '}— toggle to 1 in TMSH to enable debug logging.
                                    </div>
                                ) : (
                                    <div className="p-4 space-y-5">

                                        {/* Conditions */}
                                        <div className="space-y-2">
                                            <div className="flex items-center justify-between">
                                                <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Conditions</span>
                                                {condCmds.length > 0 && (
                                                    <button onClick={() => addCondition(block.id)} className="text-xs text-amber-600 dark:text-amber-400 hover:underline flex items-center gap-0.5">
                                                        <Plus className="h-3 w-3" /> Add condition
                                                    </button>
                                                )}
                                            </div>
                                            {condCmds.length === 0 && (
                                                <p className="text-xs text-slate-400 italic">No conditions available for this event.</p>
                                            )}
                                            {block.conditions.map(cond => (
                                                <div key={cond.id} className="flex gap-2 items-center">
                                                    <select
                                                        className={`flex-1 font-mono ${sel}`}
                                                        value={cond.command}
                                                        onChange={e => updateCondition(block.id, cond.id, 'command', e.target.value)}
                                                    >
                                                        <option value="">-- command --</option>
                                                        {condCmds.map(c => <option key={c} value={c}>{c}</option>)}
                                                    </select>
                                                    <select
                                                        className={`w-28 ${sel}`}
                                                        value={cond.operator}
                                                        onChange={e => updateCondition(block.id, cond.id, 'operator', e.target.value)}
                                                    >
                                                        {OPERATORS.map(o => <option key={o}>{o}</option>)}
                                                    </select>
                                                    <input
                                                        className={`flex-1 ${sel}`}
                                                        placeholder="value"
                                                        value={cond.value}
                                                        onChange={e => updateCondition(block.id, cond.id, 'value', e.target.value)}
                                                    />
                                                    <button onClick={() => removeCondition(block.id, cond.id)} className="text-slate-400 hover:text-red-500 shrink-0">
                                                        <Trash2 className="h-3.5 w-3.5" />
                                                    </button>
                                                </div>
                                            ))}
                                        </div>

                                        {/* Actions */}
                                        <div className="space-y-2">
                                            <div className="flex items-center justify-between">
                                                <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">Actions</span>
                                                {actCmds.length > 0 && (
                                                    <button onClick={() => addAction(block.id)} className="text-xs text-amber-600 dark:text-amber-400 hover:underline flex items-center gap-0.5">
                                                        <Plus className="h-3 w-3" /> Add action
                                                    </button>
                                                )}
                                            </div>
                                            {actCmds.length === 0 && (
                                                <p className="text-xs text-slate-400 italic">No actions defined for this event.</p>
                                            )}
                                            {block.actions.map(act => (
                                                <div key={act.id} className="flex gap-2 items-center">
                                                    <select
                                                        className={`flex-1 font-mono ${sel}`}
                                                        value={act.command}
                                                        onChange={e => updateAction(block.id, act.id, 'command', e.target.value)}
                                                    >
                                                        <option value="">-- command --</option>
                                                        {actCmds.map(c => <option key={c} value={c}>{c}</option>)}
                                                    </select>
                                                    <input
                                                        className={`flex-1 ${sel}`}
                                                        placeholder="argument / value"
                                                        value={act.value}
                                                        onChange={e => updateAction(block.id, act.id, 'value', e.target.value)}
                                                    />
                                                    <button onClick={() => removeAction(block.id, act.id)} className="text-slate-400 hover:text-red-500 shrink-0">
                                                        <Trash2 className="h-3.5 w-3.5" />
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )
                    })}

                    {/* Add event picker */}
                    <div className="bg-white dark:bg-slate-800 p-4 rounded-xl border border-dashed border-slate-300 dark:border-slate-600 shadow-sm">
                        <select
                            className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 p-2 text-sm text-slate-900 dark:text-slate-100 font-mono focus:ring-2 focus:ring-amber-500 outline-none"
                            defaultValue=""
                            onChange={e => { if (e.target.value) { addEvent(e.target.value); e.target.value = '' } }}
                        >
                            <option value="" disabled>+ Add event block…</option>
                            {availableEvents.map(ev => (
                                <option key={ev} value={ev}>{ev}</option>
                            ))}
                        </select>
                        <p className="text-xs text-slate-400 mt-2">
                            Only events valid for <span className="font-medium text-slate-600 dark:text-slate-300">{protocol}</span> are listed. Each event can only appear once.
                        </p>
                    </div>

                    {/* Dependencies */}
                    <div className="bg-white dark:bg-slate-800 p-4 rounded-xl border dark:border-slate-700 shadow-sm space-y-1.5">
                        <label className="text-sm font-medium text-slate-900 dark:text-slate-200">
                            Additional Context / Dependencies
                        </label>
                        <input
                            type="text"
                            placeholder="e.g. Uses Data Group 'allowed_ips', Requires persistence profile…"
                            className="w-full rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 p-2 text-sm text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-amber-500 outline-none placeholder:text-slate-400 dark:placeholder:text-slate-500"
                            value={dependencies}
                            onChange={e => setDependencies(e.target.value)}
                        />
                    </div>

                    {/* Enhance button */}
                    <button
                        disabled={!hasBlocks || generating}
                        onClick={handleEnhance}
                        className="w-full bg-amber-600 hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-2.5 rounded-lg transition-colors flex justify-center items-center gap-2"
                    >
                        <Sparkles className="h-4 w-4" />
                        {generating ? 'Enhancing with AI…' : 'Enhance with AI'}
                    </button>

                    {aiError && (
                        <div className="flex items-start gap-3 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
                            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                            {aiError}
                        </div>
                    )}
                </div>

                {/* ── Right: Live Preview ────────────────────────────────────── */}
                <div className="sticky top-4">
                    <div className="bg-slate-900 dark:bg-slate-950 rounded-xl border border-slate-700 shadow-sm overflow-hidden">
                        <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-700">
                            <div className="flex items-center gap-2">
                                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                                    Live Preview
                                </span>
                                {enhancedCode && (
                                    <span className="text-xs bg-amber-600/20 text-amber-400 border border-amber-600/30 px-1.5 py-0.5 rounded font-medium">
                                        AI Enhanced
                                    </span>
                                )}
                            </div>
                            {hasBlocks && (
                                <button
                                    onClick={handleCopy}
                                    className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 transition-colors"
                                >
                                    {copied ? <Check className="h-3.5 w-3.5 text-green-400" /> : <Copy className="h-3.5 w-3.5" />}
                                    {copied ? 'Copied' : 'Copy'}
                                </button>
                            )}
                        </div>
                        <pre className="p-4 text-xs font-mono text-slate-100 overflow-auto max-h-[75vh] whitespace-pre leading-relaxed">
                            {hasBlocks
                                ? displayCode
                                : <span className="text-slate-500 italic">Add event blocks on the left to see the iRule build here…</span>
                            }
                        </pre>
                    </div>

                    {!hasBlocks && (
                        <div className="mt-4 bg-slate-50 dark:bg-slate-900/50 p-4 rounded-xl border dark:border-slate-700 text-sm text-slate-500 dark:text-slate-400 space-y-2">
                            <div className="flex items-start gap-2">
                                <AlertCircle className="h-4 w-4 mt-0.5 text-amber-500 shrink-0" />
                                <div>
                                    <p className="font-semibold text-slate-700 dark:text-slate-300 mb-1">How to use</p>
                                    <ol className="list-decimal pl-4 space-y-1 text-xs">
                                        <li>Select a TMOS version and protocol above</li>
                                        <li>Pick event blocks from the dropdown — only valid events for that protocol are shown</li>
                                        <li>For each event, add conditions (if-check) and actions (what to do)</li>
                                        <li>The iRule assembles live in this panel</li>
                                        <li>Hit <strong>Enhance with AI</strong> to have Ollama complete and harden the skeleton</li>
                                    </ol>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

            </div>
        </div>
    )
}
