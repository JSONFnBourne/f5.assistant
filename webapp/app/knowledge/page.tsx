'use client';

import React, { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { Send, Bot, User, Loader2 } from 'lucide-react';
import type { QueryMode } from '@/lib/knowledgeClassifier';

interface Source {
  title: string;
  url: string;
}

interface Message {
  role: 'user' | 'bot';
  text: string;
  sources?: Source[];
  mode?: QueryMode;
  streaming?: boolean;
}

const MODE_LABELS: Record<QueryMode, string> = {
  f5:      'F5 Context',
  rfc:     'RFC Context',
  general: 'General',
};

const MODE_STYLES: Record<QueryMode, string> = {
  f5:      'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400',
  rfc:     'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400',
  general: 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300',
};

export default function KnowledgePage() {
  const [messages, setMessages] = useState<Message[]>([
    { role: 'bot', text: 'Ask me anything about F5 BIG-IP — TMSH, iRules, LTM, DNS, AFM, APM, ASM, SSLO, VELOS, or rSeries. I answer strictly from F5 documentation with source citations.' }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [mounted, setMounted] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [input]);

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!input.trim() || loading) return;

    const userMessage = input;
    setInput('');
    setMessages(prev => [...prev, { role: 'user', text: userMessage }]);
    setLoading(true);

    try {
      const resp = await fetch('/api/knowledge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMessage }),
      });

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setMessages(prev => [...prev, {
          role: 'bot',
          text: `Error: ${data.error || 'Could not reach the local knowledge service.'}`,
        }]);
        return;
      }

      if (!resp.body) {
        setMessages(prev => [...prev, { role: 'bot', text: 'Error: No response body received.' }]);
        return;
      }

      // Add a placeholder streaming message
      setMessages(prev => [...prev, { role: 'bot', text: '', streaming: true }]);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let metaParsed = false;
      let lineBuffer = '';
      let streamedMode: QueryMode | undefined;
      let streamedSources: Source[] | undefined;

      const appendText = (chunk: string) => {
        setMessages(prev => {
          const msgs = [...prev];
          const last = { ...msgs[msgs.length - 1] };
          last.text += chunk;
          msgs[msgs.length - 1] = last;
          return msgs;
        });
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });

        if (!metaParsed) {
          lineBuffer += chunk;
          const nlIdx = lineBuffer.indexOf('\n');
          if (nlIdx !== -1) {
            const metaLine = lineBuffer.substring(0, nlIdx);
            const rest = lineBuffer.substring(nlIdx + 1);
            try {
              const meta = JSON.parse(metaLine);
              if (meta.__meta) {
                streamedMode = meta.mode as QueryMode;
                streamedSources = meta.sources as Source[];
                // Apply metadata to the placeholder message
                setMessages(prev => {
                  const msgs = [...prev];
                  msgs[msgs.length - 1] = {
                    role: 'bot',
                    text: rest,
                    streaming: true,
                    mode: streamedMode,
                    sources: streamedSources,
                  };
                  return msgs;
                });
                setLoading(false);
                metaParsed = true;
                lineBuffer = '';
                continue;
              }
            } catch { /* not JSON — fall through and treat as text */ }
            // If meta parse failed, treat the whole buffer as text
            metaParsed = true;
            setLoading(false);
            appendText(lineBuffer);
            lineBuffer = '';
          }
        } else {
          appendText(chunk);
        }
      }

      // If we never saw a newline (very short response), flush the buffer
      if (!metaParsed && lineBuffer) {
        setLoading(false);
        appendText(lineBuffer);
      }

      // Mark streaming complete — sources are already set; finalize message
      setMessages(prev => {
        const msgs = [...prev];
        const last = { ...msgs[msgs.length - 1] };
        last.streaming = false;
        // Ensure mode/sources are set if they arrived
        if (streamedMode) last.mode = streamedMode;
        if (streamedSources) last.sources = streamedSources;
        msgs[msgs.length - 1] = last;
        return msgs;
      });

    } catch (error) {
      setLoading(false);
      setMessages(prev => {
        const msgs = [...prev];
        const last = msgs[msgs.length - 1];
        if (last?.role === 'bot' && last.streaming) {
          // Replace mid-stream placeholder with error
          msgs[msgs.length - 1] = { role: 'bot', text: 'Stream interrupted. Check that Ollama is running.' };
        } else {
          msgs.push({ role: 'bot', text: 'Network error. Check that Ollama is running.' });
        }
        return msgs;
      });
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="h-[calc(100vh-140px)] flex flex-col rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden bg-slate-50 dark:bg-slate-900">
      <header className="flex items-center gap-3 px-6 py-4 border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 flex-none">
        <Bot size={28} className="text-amber-500" />
        <div>
          <h1 className="text-lg font-bold text-slate-900 dark:text-slate-100">Knowledge Base</h1>
          <p className="text-xs text-slate-500 dark:text-slate-400">F5 BIG-IP, iRules, TMSH, LTM, DNS, AFM, APM, ASM, SSLO, VELOS, rSeries — strictly grounded, local Ollama</p>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-6 space-y-4" ref={scrollRef}>
        {mounted && messages.map((m, i) => (
          <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            {m.role === 'bot' && (
              <div className="w-8 h-8 rounded-full bg-amber-100 dark:bg-amber-900/50 flex items-center justify-center flex-shrink-0">
                <Bot className="h-5 w-5 text-amber-600 dark:text-amber-400" />
              </div>
            )}
            <div className={`max-w-[80%] min-w-0 overflow-hidden rounded-2xl p-4 text-sm shadow-sm ${m.role === 'user'
              ? 'bg-amber-500 text-white rounded-br-none'
              : 'bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 border dark:border-slate-700 rounded-bl-none'
            }`}>
              {m.role === 'bot' && m.mode && (
                <span className={`inline-block text-xs font-semibold px-2 py-0.5 rounded-full mb-2 ${MODE_STYLES[m.mode]}`}>
                  {MODE_LABELS[m.mode]}
                </span>
              )}
              <div className="prose prose-sm dark:prose-invert max-w-none break-words overflow-x-hidden">
                <ReactMarkdown>{m.text}</ReactMarkdown>
                {m.streaming && m.text === '' && (
                  <span className="inline-flex items-center gap-1 text-slate-400 dark:text-slate-500 text-xs">
                    <Loader2 className="animate-spin h-3 w-3" /> thinking…
                  </span>
                )}
                {m.streaming && m.text !== '' && (
                  <span className="inline-block w-1.5 h-4 bg-amber-400 animate-pulse ml-0.5 align-middle" />
                )}
              </div>
              {!m.streaming && m.sources && m.sources.length > 0 && (
                <div className="mt-3 pt-3 border-t border-slate-200 dark:border-slate-600">
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-2 font-medium">Sources:</p>
                  <div className="flex flex-wrap gap-2">
                    {m.sources.map((s, si) => (
                      <a
                        key={si}
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs px-2 py-1 rounded-full bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-900/50 transition-colors"
                      >
                        {s.title}
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </div>
            {m.role === 'user' && (
              <div className="w-8 h-8 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center flex-shrink-0">
                <User className="h-5 w-5 text-slate-600 dark:text-slate-300" />
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex gap-3 justify-start">
            <div className="w-8 h-8 rounded-full bg-amber-100 dark:bg-amber-900/50 flex items-center justify-center flex-shrink-0">
              <Bot className="h-5 w-5 text-amber-600 dark:text-amber-400" />
            </div>
            <div className="bg-white dark:bg-slate-800 border dark:border-slate-700 rounded-2xl rounded-bl-none p-4 flex items-center gap-2">
              <Loader2 className="animate-spin h-4 w-4 text-amber-500" />
              <span className="text-sm text-slate-500 dark:text-slate-400">Searching knowledge base…</span>
            </div>
          </div>
        )}
      </div>

      <div className="flex-none px-6 py-4 border-t border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950">
        <form onSubmit={handleSubmit} className="relative">
          <textarea
            ref={textareaRef}
            placeholder="Ask about F5 configuration, iRules, TMSH, or protocol standards… (Enter to send, Shift+Enter for newline)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
            rows={1}
            className="w-full pl-4 pr-12 py-3 rounded-xl border border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:border-amber-500 dark:focus:border-amber-400 focus:ring-2 focus:ring-amber-100 dark:focus:ring-amber-900 outline-none placeholder:text-slate-400 dark:placeholder:text-slate-500 resize-none min-h-[50px] max-h-[200px]"
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="absolute right-2 top-2 p-1.5 bg-amber-500 hover:bg-amber-600 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="h-5 w-5" />
          </button>
        </form>
      </div>
    </div>
  );
}
