'use client'

import React, { useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, CheckCircle, ShieldCheck } from 'lucide-react'

import { validateIRule, ValidationResult } from '@/app/lib/validator'

export default function ValidatorPage() {
    const [code, setCode] = useState('')
    const [result, setResult] = useState<ValidationResult | null>(null)

    const handleValidate = () => {
        const analysis = validateIRule(code)
        setResult(analysis)
    }

    return (
        <div className="max-w-5xl mx-auto space-y-8">
            <div>
                <Link href="/" className="inline-flex items-center text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200 mb-4 transition-colors">
                    <ArrowLeft className="h-4 w-4 mr-1" /> Back to dashboard
                </Link>
                <h1 className="text-3xl font-bold text-slate-900 dark:text-slate-100 flex items-center gap-3">
                    <ShieldCheck className="h-8 w-8 text-blue-600 dark:text-blue-500" />
                    iRule Validator
                </h1>
                <p className="text-slate-600 dark:text-slate-400 mt-2">
                    Check your iRules for syntax errors, event context violations, and security risks.
                </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                <div className="bg-white dark:bg-slate-800 p-4 rounded-xl border dark:border-slate-700 shadow-sm flex flex-col h-[600px]">
                    <div className="flex items-center justify-between mb-2">
                        <label className="text-sm font-medium text-slate-700 dark:text-slate-200">iRule Source Code</label>
                        <span className="text-xs text-slate-400">TCL / iRule</span>
                    </div>
                    <textarea
                        className="flex-1 w-full p-4 font-mono text-sm bg-slate-900 text-slate-50 rounded-lg resize-none outline-none focus:ring-2 focus:ring-blue-500 border border-transparent dark:border-slate-700"
                        placeholder="when HTTP_REQUEST { ... }"
                        value={code}
                        onChange={(e) => setCode(e.target.value)}
                        spellCheck={false}
                    />
                    <div className="mt-4">
                        <button
                            onClick={handleValidate}
                            className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 rounded-lg transition-colors flex justify-center items-center gap-2"
                        >
                            <CheckCircle className="h-4 w-4" /> Analyze
                        </button>
                    </div>
                </div>

                <div className="bg-slate-50 dark:bg-slate-900/50 p-6 rounded-xl border dark:border-slate-700 h-[600px] overflow-auto">
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-4">Analysis Report</h2>
                    {!result ? (
                        <div className="flex flex-col items-center justify-center h-64 text-slate-400 dark:text-slate-500 border-2 border-dashed dark:border-slate-700 rounded-lg">
                            <span className="text-sm">Run analysis to see results</span>
                        </div>
                    ) : (
                        <div className="space-y-6">
                            <div className={`p-4 rounded-lg border ${result.isValid
                                ? 'bg-green-50 border-green-200 text-green-800 dark:bg-green-900/20 dark:border-green-800 dark:text-green-300'
                                : 'bg-red-50 border-red-200 text-red-800 dark:bg-red-900/20 dark:border-red-800 dark:text-red-300'
                                }`}>
                                <div className="font-bold flex items-center gap-2">
                                    {result.isValid ? <CheckCircle className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
                                    {result.isValid ? 'Valid Logic' : 'Issues Detected'}
                                </div>
                                <div className="text-sm mt-1">
                                    {result.errors.length} Errors, {result.warnings.length} Warnings
                                </div>
                            </div>

                            {result.profilesRequired.length > 0 && (
                                <div>
                                    <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-200 uppercase tracking-wider mb-2">Required Profiles</h3>
                                    <div className="flex flex-wrap gap-2">
                                        {result.profilesRequired.map(p => (
                                            <span key={p} className="px-2 py-1 bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-200 rounded text-xs font-semibold">
                                                {p}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {result.errors.length > 0 && (
                                <div>
                                    <h3 className="text-sm font-semibold text-red-700 dark:text-red-400 uppercase tracking-wider mb-2">Errors</h3>
                                    <ul className="space-y-2">
                                        {result.errors.map((err, i) => (
                                            <li key={i} className="text-sm text-red-600 dark:text-red-300 bg-red-50 dark:bg-red-900/10 p-2 rounded border border-red-100 dark:border-red-900/30">
                                                {err}
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            )}

                            {result.warnings.length > 0 && (
                                <div>
                                    <h3 className="text-sm font-semibold text-amber-700 dark:text-amber-400 uppercase tracking-wider mb-2">Warnings & Best Practices</h3>
                                    <ul className="space-y-2">
                                        {result.warnings.map((warn, i) => (
                                            <li key={i} className="text-sm text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/10 p-2 rounded border border-amber-100 dark:border-amber-900/30">
                                                {warn}
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
