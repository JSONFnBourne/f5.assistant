import type { Metadata } from 'next'
import Link from 'next/link'
import { Inter } from 'next/font/google'
import { ThemeProvider } from './components/ThemeProvider'
import { ModeToggle } from './components/ModeToggle'
import './globals.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
    title: 'F5 Assistant',
    description: 'F5 Assistant — BIG-IP knowledge, iRules, QKView analysis, and RFC documentation',
}

export default function RootLayout({
    children,
}: {
    children: React.ReactNode
}) {
    return (
        <html lang="en" suppressHydrationWarning>
            <body className={inter.className}>
                <ThemeProvider
                    attribute="class"
                    defaultTheme="system"
                    enableSystem
                    disableTransitionOnChange
                >
                    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 text-slate-900 dark:text-slate-50 transition-colors">
                        <header className="border-b bg-white dark:bg-slate-950 dark:border-slate-800 px-6 py-4 shadow-sm">
                            <div className="mx-auto max-w-7xl flex items-center justify-between">
                                <h1 className="text-xl font-bold tracking-tight text-slate-800 dark:text-slate-100">F5 Assistant</h1>
                                <div className="flex items-center gap-6">
                                    <nav className="space-x-4 text-sm font-medium text-slate-600 dark:text-slate-400">
                                        <Link href="/" className="hover:text-amber-600 dark:hover:text-amber-400 transition-colors">Home</Link>
                                        <Link href="/qkview" className="hover:text-amber-600 dark:hover:text-amber-400 transition-colors">QKView Analyzer</Link>
                                        <Link href="/knowledge" className="hover:text-amber-600 dark:hover:text-amber-400 transition-colors">Knowledge Base</Link>
                                        <Link href="/reference" className="hover:text-amber-600 dark:hover:text-amber-400 transition-colors">Index</Link>
                                        <Link href="/generator" className="hover:text-amber-600 dark:hover:text-amber-400 transition-colors">iRule Generator</Link>
                                        <Link href="/validator" className="hover:text-amber-600 dark:hover:text-amber-400 transition-colors">Validator</Link>
                                    </nav>
                                    <ModeToggle />
                                </div>
                            </div>
                        </header>
                        <main className="mx-auto max-w-7xl p-6">
                            {children}
                        </main>
                    </div>
                </ThemeProvider>
            </body>
        </html>
    )
}
