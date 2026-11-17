import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Loader2, Sparkles } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import type { BibleStudyDepth } from '../../types/api';
import ReferenceChip from './ReferenceChip';

const DEPTHS: { id: BibleStudyDepth; label: string }[] = [
  { id: 'adult', label: 'Adult' },
  { id: 'teen', label: 'Teen' },
  { id: 'child', label: 'Child' },
];

interface Props {
  /** The search text, offered as the question. */
  question: string;
  version: string;
  /** The search's "where" filter (`in:` value), so the answer looks in the same place. */
  scope?: string;
  onOpen: (reference: string) => void;
}

/**
 * Ask Jarvis a question of the whole Bible. The server finds the verses (by
 * meaning when it can), the model answers from them, and only references
 * that exist come back, so every chip opens a real passage.
 */
export default function BibleSearchAsk({ question: initial, version, scope, onOpen }: Props) {
  const [question, setQuestion] = useState(initial);
  const [depth, setDepth] = useState<BibleStudyDepth>('adult');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => inputRef.current?.focus(), []);
  const ask = useMutation({
    mutationFn: () => api.askBibleSearch(question.trim(), { version, scope: scope || undefined, depth }),
  });
  const result = ask.data;

  return (
    <div className="mb-3 rounded-xl border border-sky-400/20 bg-sky-500/[0.06] p-3" data-testid="bible-search-ask">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (question.trim().length >= 2 && !ask.isPending) ask.mutate();
        }}
        className="space-y-2"
      >
        <label htmlFor="bible-search-ask-question" className="block text-xs font-semibold text-sky-100">
          Ask Jarvis about the whole Bible
        </label>
        <textarea
          id="bible-search-ask-question"
          ref={inputRef}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={2}
          maxLength={2000}
          className="w-full min-w-0 rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-base text-slate-100 sm:text-sm"
          data-testid="bible-search-ask-input"
        />
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-full border border-white/10 p-0.5" role="group" aria-label="Answer for">
            {DEPTHS.map((d) => (
              <button
                key={d.id}
                type="button"
                aria-pressed={depth === d.id}
                onClick={() => setDepth(d.id)}
                className={`min-h-11 rounded-full px-3 text-xs font-semibold ${
                  depth === d.id ? 'bg-sky-500 text-slate-950' : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {d.label}
              </button>
            ))}
          </div>
          <button
            type="submit"
            disabled={ask.isPending || question.trim().length < 2}
            className="glass-button ml-auto min-h-11 px-4 text-sm font-semibold inline-flex items-center gap-2 disabled:opacity-40"
            data-testid="bible-search-ask-go"
          >
            {ask.isPending ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            Ask
          </button>
        </div>
      </form>

      {ask.isPending ? (
        <p className="mt-3 text-sm text-slate-400">Finding verses and writing an answer… this can take a minute.</p>
      ) : ask.error ? (
        <p className="mt-3 text-sm text-rose-300" role="alert" data-testid="bible-search-ask-error">
          {errorText(ask.error, 'Jarvis could not answer just now.')}
        </p>
      ) : result ? (
        <div className="mt-3 space-y-2" data-testid="bible-search-ask-answer">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{result.answer}</p>
          {result.references.length ? (
            <div className="flex flex-wrap gap-1.5">
              {result.references.map((r) => (
                <ReferenceChip key={r.reference} reference={r.reference} version={result.version} onOpenRef={onOpen} />
              ))}
            </div>
          ) : null}
          <p className="text-[11px] text-slate-500">
            From {result.verses.length} verses found by {result.retrieval === 'meaning' ? 'meaning' : 'their words'}.
            Check what Jarvis says against the verses themselves.
          </p>
        </div>
      ) : null}
    </div>
  );
}
