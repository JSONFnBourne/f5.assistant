import Link from 'next/link'
import { ArrowRight, Code, CheckCircle, BookOpen, Database, FileSearch } from 'lucide-react'

export default function Home() {
    return (
        <div className="py-12 space-y-10">
            <div className="text-center space-y-4">
                <h1 className="text-4xl font-extrabold tracking-tight lg:text-5xl text-slate-900 dark:text-slate-50">
                    F5 Assistant
                </h1>
                <p className="mx-auto max-w-2xl text-lg text-slate-600 dark:text-slate-400">
                    F5 BIG-IP knowledge base, iRules tooling, and QKView diagnostics — all local, all private.
                </p>
            </div>

            <div className="grid md:grid-cols-2 gap-8 max-w-4xl mx-auto">

                <Link href="/qkview" className="group relative block p-8 bg-white dark:bg-slate-800 rounded-xl shadow-sm border dark:border-slate-700 hover:border-violet-500 dark:hover:border-violet-500 hover:shadow-md transition-all md:col-span-2">
                    <div className="flex items-center gap-4 mb-4">
                        <div className="p-3 rounded-lg bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-400">
                            <FileSearch className="h-6 w-6" />
                        </div>
                        <h3 className="text-xl font-semibold text-slate-900 dark:text-slate-100">QKView Log Analyzer</h3>
                    </div>
                    <p className="text-slate-600 dark:text-slate-400 mb-6">
                        Upload a BIG-IP QKView archive to parse logs, index device configuration, and scan for known issues via the automated Python backend.
                    </p>
                    <div className="flex items-center text-violet-600 dark:text-violet-400 font-medium group-hover:translate-x-1 transition-transform">
                        Launch Analyzer <ArrowRight className="ml-2 h-4 w-4" />
                    </div>
                </Link>

                <Link href="/knowledge" className="group relative block p-8 bg-white dark:bg-slate-800 rounded-xl shadow-sm border dark:border-slate-700 hover:border-amber-500 dark:hover:border-amber-500 hover:shadow-md transition-all md:col-span-2">
                    <div className="flex items-center gap-4 mb-4">
                        <div className="p-3 rounded-lg bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400">
                            <Database className="h-6 w-6" />
                        </div>
                        <h3 className="text-xl font-semibold text-slate-900 dark:text-slate-100">Knowledge Base</h3>
                    </div>
                    <p className="text-slate-600 dark:text-slate-400 mb-6">
                        Ask any F5 question — BIG-IP, TMSH, iRules, LTM, DNS, AFM, APM, ASM, SSLO, VELOS, and rSeries. Answers are strictly grounded in F5 documentation with full source citations. No guessing.
                    </p>
                    <div className="flex items-center text-amber-600 dark:text-amber-400 font-medium group-hover:translate-x-1 transition-transform">
                        Ask a Question <ArrowRight className="ml-2 h-4 w-4" />
                    </div>
                </Link>

                <Link href="/reference" className="group relative block p-8 bg-white dark:bg-slate-800 rounded-xl shadow-sm border dark:border-slate-700 hover:border-emerald-500 dark:hover:border-emerald-500 hover:shadow-md transition-all">
                    <div className="flex items-center gap-4 mb-4">
                        <div className="p-3 rounded-lg bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400">
                            <BookOpen className="h-6 w-6" />
                        </div>
                        <h3 className="text-xl font-semibold text-slate-900 dark:text-slate-100">iRule Index</h3>
                    </div>
                    <p className="text-slate-600 dark:text-slate-400 mb-6">
                        Alphabetical and hierarchical browser for Commands, Events, and Operators with plain language explanations.
                    </p>
                    <div className="flex items-center text-emerald-600 dark:text-emerald-400 font-medium group-hover:translate-x-1 transition-transform">
                        Browse Index <ArrowRight className="ml-2 h-4 w-4" />
                    </div>
                </Link>

                <Link href="/generator" className="group relative block p-8 bg-white dark:bg-slate-800 rounded-xl shadow-sm border dark:border-slate-700 hover:border-amber-500 dark:hover:border-amber-500 hover:shadow-md transition-all">
                    <div className="flex items-center gap-4 mb-4">
                        <div className="p-3 rounded-lg bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400">
                            <Code className="h-6 w-6" />
                        </div>
                        <h3 className="text-xl font-semibold text-slate-900 dark:text-slate-100">iRule Generator</h3>
                    </div>
                    <p className="text-slate-600 dark:text-slate-400 mb-6">
                        Guide-driven generation of safe, performant iRules. Ensures correct event usage and profile dependencies.
                    </p>
                    <div className="flex items-center text-amber-600 dark:text-amber-400 font-medium group-hover:translate-x-1 transition-transform">
                        Start Generator <ArrowRight className="ml-2 h-4 w-4" />
                    </div>
                </Link>

                <Link href="/validator" className="group relative block p-8 bg-white dark:bg-slate-800 rounded-xl shadow-sm border dark:border-slate-700 hover:border-blue-500 dark:hover:border-blue-500 hover:shadow-md transition-all md:col-span-2">
                    <div className="flex items-center gap-4 mb-4">
                        <div className="p-3 rounded-lg bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400">
                            <CheckCircle className="h-6 w-6" />
                        </div>
                        <h3 className="text-xl font-semibold text-slate-900 dark:text-slate-100">iRule Validator</h3>
                    </div>
                    <p className="text-slate-600 dark:text-slate-400 mb-6">
                        Paste an iRule to receive feedback. Checks for deprecated commands, logic errors, and security risks.
                    </p>
                    <div className="flex items-center text-blue-600 dark:text-blue-400 font-medium group-hover:translate-x-1 transition-transform">
                        Validate Code <ArrowRight className="ml-2 h-4 w-4" />
                    </div>
                </Link>

            </div>
        </div>
    )
}
