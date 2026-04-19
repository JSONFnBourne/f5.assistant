'use client'
import React, { useState, useMemo } from 'react'
import Link from 'next/link'
import { ArrowLeft, Search, BookOpen, Zap, Terminal } from 'lucide-react'
import { getReferenceItems, ReferenceItem } from '@/app/lib/referenceData'

export default function ReferencePage() {
    const [filter, setFilter] = useState('')
    const [activeTab, setActiveTab] = useState<'All' | 'Command' | 'Event' | 'Operator'>('All')

    const items = useMemo(() => getReferenceItems(activeTab === 'All' ? undefined : activeTab), [activeTab])

    const filteredItems = useMemo(() => {
        const lowerFilter = filter.toLowerCase();
        if (!lowerFilter) return items;
        return items.filter(item =>
            item.name.toLowerCase().includes(lowerFilter) ||
            item.summary.toLowerCase().includes(lowerFilter)
        )
    }, [items, filter])

    const getIcon = (type: string) => {
        switch (type) {
            case 'Event': return <Zap className="h-4 w-4 text-yellow-600" />
            case 'Command': return <Terminal className="h-4 w-4 text-blue-600" />
            case 'Operator': return <BookOpen className="h-4 w-4 text-green-600" />
            default: return <BookOpen className="h-4 w-4" />
        }
    }

    return (
        <div className="min-h-screen bg-white dark:bg-slate-900 transition-colors">
            <header className="border-b bg-white dark:bg-slate-950 dark:border-slate-800">
                <div className="container mx-auto px-4 h-16 flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <Link href="/" className="text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 transition-colors">
                            <ArrowLeft className="h-5 w-5" />
                        </Link>
                        <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100">iRule Index</h1>
                    </div>
                </div>
            </header>

            <main className="container mx-auto px-4 py-8">
                <div className="max-w-4xl mx-auto space-y-6">

                    <div className="flex flex-col md:flex-row gap-4">
                        <div className="relative flex-1">
                            <Search className="absolute left-3 top-2.5 h-5 w-5 text-slate-400" />
                            <input
                                type="text"
                                placeholder="Search commands, events..."
                                className="w-full pl-10 pr-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none bg-white dark:bg-slate-800 border-slate-300 dark:border-slate-700 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500"
                                value={filter}
                                onChange={(e) => setFilter(e.target.value)}
                            />
                        </div>
                        <div className="flex gap-2">
                            {['All', 'Command', 'Event', 'Operator'].map((tab) => (
                                <button
                                    key={tab}
                                    onClick={() => setActiveTab(tab as any)}
                                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${activeTab === tab
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
                                        }`}
                                >
                                    {tab}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="grid gap-4">
                        {filteredItems.map((item, idx) => (
                            <div key={idx} className="p-4 border rounded-xl hover:shadow-sm transition-shadow bg-white dark:bg-slate-800 dark:border-slate-700">
                                <div className="flex items-start justify-between">
                                    <div className="flex items-center gap-2 mb-1">
                                        {getIcon(item.type)}
                                        <span className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">{item.type}</span>
                                    </div>
                                </div>
                                <h3 className="text-lg font-mono font-bold text-blue-700 dark:text-blue-400 mb-2">{item.name}</h3>
                                <p className="text-slate-600 dark:text-slate-300 text-sm">{item.summary}</p>
                            </div>
                        ))}

                        {filteredItems.length === 0 && (
                            <div className="text-center py-12 text-slate-500 dark:text-slate-400">
                                No items found matching your search.
                            </div>
                        )}
                    </div>

                </div>
            </main>
        </div>
    )
}
