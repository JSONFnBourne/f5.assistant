'use client'
import React, { useState, useRef, useEffect } from 'react'
import Link from 'next/link'
import { ArrowLeft, Send, MessageSquare, Bot, User } from 'lucide-react'

export type Message = {
    role: 'user' | 'assistant';
    content: string;
    timestamp: number;
};

export default function DiscussionPage() {
    const [input, setInput] = useState('')
    const [messages, setMessages] = useState<Message[]>([
        { role: 'assistant', content: 'Hello! I am your F5 Assistant. Paste an iRule, syslog, or ask me a question about syntax, architectures, or security.', timestamp: Date.now() }
    ])
    const messagesEndRef = useRef<HTMLDivElement>(null)

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
    }

    useEffect(() => {
        scrollToBottom()
    }, [messages])

    const handleSend = async (e: React.FormEvent) => {
        e.preventDefault()
        if (!input.trim()) return

        const userMsg: Message = { role: 'user', content: input, timestamp: Date.now() }
        const newMessages = [...messages, userMsg]
        setMessages(newMessages)
        setInput('')

        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ messages: newMessages })
            })

            if (!res.ok) {
                const errJson = await res.json().catch(() => ({}));
                setMessages(prev => [...prev, {
                    role: 'assistant',
                    content: `Error: ${errJson.error || 'Network error communicating with the local search service.'}`,
                    timestamp: Date.now()
                }])
                return
            }

            // Create a placeholder message for the assistant stream
            setMessages(prev => [...prev, { role: 'assistant', content: '', timestamp: Date.now() }])

            // Read the real-time stream
            const reader = res.body?.getReader()
            const decoder = new TextDecoder()
            let aiText = ''

            if (reader) {
                while (true) {
                    const { value, done } = await reader.read()
                    if (done) break

                    aiText += decoder.decode(value, { stream: true })

                    // Update the last message (the placeholder) with the newly appended text chunk
                    setMessages(prev => {
                        const newMsgs = [...prev]
                        newMsgs[newMsgs.length - 1].content = aiText
                        return newMsgs
                    })
                }
            }
        } catch (error) {
            console.error('Failed to parse AI stream:', error)
            setMessages(prev => {
                // Determine if we crash mid-stream or before starting
                const newMsgs = [...prev];
                const lastMsg = newMsgs[newMsgs.length - 1];
                if (lastMsg.role === 'assistant' && lastMsg.content === '') {
                    lastMsg.content = 'Network error streaming from Assistant.'
                } else if (lastMsg.role !== 'assistant') {
                    newMsgs.push({ role: 'assistant', content: 'Network stream failed.', timestamp: Date.now() })
                }
                return newMsgs;
            });
        }
    }

    return (
        <div className="h-[calc(100vh-140px)] bg-slate-50 dark:bg-slate-900 flex flex-col transition-colors overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
            <header className="bg-white dark:bg-slate-950 border-b dark:border-slate-800 flex-none">
                <div className="container mx-auto px-4 h-16 flex items-center gap-4">
                    <Link href="/" className="text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200 transition-colors">
                        <ArrowLeft className="h-5 w-5" />
                    </Link>
                    <div>
                        <h1 className="text-lg font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
                            <MessageSquare className="h-5 w-5 text-indigo-600 dark:text-indigo-400" />
                            F5 Assistant Chat
                        </h1>
                        <p className="text-xs text-slate-500 dark:text-slate-400">Interactive Assistant</p>
                    </div>
                </div>
            </header>

            <main className="flex-1 flex flex-col container mx-auto max-w-4xl p-4 overflow-hidden">
                <div className="flex-1 overflow-y-auto space-y-4 pr-2 pb-4">
                    {messages.map((msg, idx) => (
                        <div key={idx} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                            {msg.role === 'assistant' && (
                                <div className="w-8 h-8 rounded-full bg-indigo-100 dark:bg-indigo-900/50 flex items-center justify-center flex-shrink-0">
                                    <Bot className="h-5 w-5 text-indigo-600 dark:text-indigo-400" />
                                </div>
                            )}

                            <div className={`max-w-[80%] rounded-2xl p-4 text-sm shadow-sm overflow-hidden ${msg.role === 'user'
                                ? 'bg-indigo-600 text-white rounded-br-none'
                                : 'bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 border dark:border-slate-700 rounded-bl-none'
                                }`}>
                                {msg.content.split('```').map((part, i) => {
                                    if (i % 2 === 1) {
                                        // Code block
                                        const lines = part.trim().split('\n')
                                        // If first line is just language name, skip it for display if you want, 
                                        // or just render all. Let's try to strip language if present.
                                        const hasLang = /^[a-z]+$/.test(lines[0].trim())
                                        const code = hasLang ? lines.slice(1).join('\n') : lines.join('\n')

                                        return (
                                            <pre key={i} className="bg-slate-900 text-slate-50 p-3 rounded-lg my-2 overflow-x-auto font-mono text-xs">
                                                <code>{code}</code>
                                            </pre>
                                        )
                                    }
                                    // Regular text with basic bold parsing
                                    const isMultiline = part.includes('\n')
                                    return (
                                        <div key={i} className={`whitespace-pre-wrap ${msg.role === 'user' && isMultiline ? 'font-mono text-xs' : ''}`}>
                                            {part.split('**').map((subPart, j) =>
                                                j % 2 === 1 ? <strong key={j}>{subPart}</strong> : subPart
                                            )}
                                        </div>
                                    )
                                })}
                            </div>

                            {msg.role === 'user' && (
                                <div className="w-8 h-8 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center flex-shrink-0">
                                    <User className="h-5 w-5 text-slate-600 dark:text-slate-300" />
                                </div>
                            )}
                        </div>
                    ))}
                    <div ref={messagesEndRef} />
                </div>

                <div className="flex-none pt-4 pb-2">
                    <form onSubmit={handleSend} className="relative">
                        <textarea
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' && !e.shiftKey) {
                                    e.preventDefault()
                                    if (input.trim()) {
                                        const formEvent = new Event('submit', { cancelable: true, bubbles: true })
                                        e.currentTarget.form?.dispatchEvent(formEvent)
                                    }
                                }
                            }}
                            rows={Math.min(10, input.split('\n').length || 1)}
                            placeholder="Ask about F5 architectures, syslog findings, or paste code (Shift+Enter for new line)..."
                            className="w-full pl-4 pr-12 py-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:border-indigo-500 dark:focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 dark:focus:ring-indigo-900 outline-none shadow-sm placeholder:text-slate-400 dark:placeholder:text-slate-500 resize-y min-h-[50px] max-h-[300px]"
                        />
                        <button
                            type="submit"
                            disabled={!input.trim()}
                            className="absolute right-2 top-2 p-1.5 bg-indigo-600 hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-600 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                            <Send className="h-5 w-5" />
                        </button>
                    </form>
                </div>
            </main>
        </div>
    )
}
