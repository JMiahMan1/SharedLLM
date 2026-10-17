import { useEffect, useState } from 'react';
import { BookOpen, RefreshCw, Sparkles, X } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleVerseContext, BibleVerseOfDay } from '../../types/api';

interface VerseContextPanelProps {
  verse: BibleVerseOfDay;
  onClose: () => void;
  /** Open a reference the note names in the reader. */
  onOpenRef?: (ref: string) => void;
}

const TOUCH = 'min-h-11 pointer-coarse:min-h-11';

/**
 * Jarvis's context note for the Verse of the Day.
 *
 * A stored note is shown at once. A verse that has never been explained asks
 * the server for one and waits with that fact on screen, rather than showing a
 * blank panel; a model that cannot answer says so and offers a retry. The note
 * is about the verse, not the day, so it can be read again when the same verse
 * comes round in another year.
 */
export default function VerseContextPanel({ verse, onClose, onOpenRef }: VerseContextPanelProps) {
  const { trigger } = useHaptics();
  const [context, setContext] = useState<BibleVerseContext | null>(verse.context ?? null);
  const [loading, setLoading] = useState(!verse.context);
  const [error, setError] = useState<string | null>(null);

  const load = async (refresh: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const note = await api.getBibleVerseContext({
        day: verse.day,
        version: verse.version,
        scope: verse.scope,
        refresh,
      });
      setContext(note);
    } catch (failure) {
      // The server's own sentence: an unreachable model and a missing corpus
      // each have a different fix, and neither is an empty note.
      setError(failure instanceof Error && failure.message ? failure.message : 'The context could not be loaded.');
    } finally {
      setLoading(false);
    }
  };

  /* eslint-disable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */
  useEffect(() => {
    // The verse is fixed for the life of this panel; loading once on mount is
    // what the card asked for. A retry or refresh calls load directly.
    if (!verse.context) void load(false);
  }, []);
  /* eslint-enable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={`Context for ${verse.reference}`}
      data-testid="bible-context-panel"
    >
      <button
        type="button"
        aria-label="Close verse context"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        data-testid="bible-context-scrim"
      />
      <div className="relative w-full sm:max-w-lg bg-slate-950/97 backdrop-blur-xl border border-white/10 border-b-0 sm:border-b sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] overflow-y-auto">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-xs font-semibold text-sky-300">
              <Sparkles size={14} />
              <span className="truncate">Context for {verse.reference}</span>
            </p>
            <p className="text-[11px] text-slate-500 mt-1 uppercase tracking-wide">
              The verse in its chapter and book
            </p>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => {
                void trigger('light');
                void load(true);
              }}
              disabled={loading}
              aria-label="Refresh context"
              title="Refresh context"
              className={`${TOUCH} min-w-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white disabled:opacity-40`}
              data-testid="bible-context-refresh"
            >
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close verse context"
              className={`${TOUCH} min-w-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white`}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <blockquote className="mb-3 rounded-xl border border-[var(--page-rule)] bg-black/20 px-3 py-2">
          <p className="text-[11px] font-semibold text-[var(--page-number)]">{verse.reference}</p>
          <p className="mt-1 font-serif text-sm leading-relaxed text-slate-200">{verse.text}</p>
        </blockquote>

        {loading ? (
          <p className="text-sm text-slate-400" data-testid="bible-context-loading">
            Reading the passage and its study notes…
          </p>
        ) : error ? (
          <div className="space-y-3" data-testid="bible-context-error">
            <p className="text-sm text-amber-300">{error}</p>
            <button
              type="button"
              onClick={() => {
                void trigger('light');
                void load(false);
              }}
              className={`${TOUCH} rounded-xl border border-amber-400/30 bg-amber-500/10 px-3 text-sm text-amber-100`}
            >
              Try again
            </button>
          </div>
        ) : context ? (
          <>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100" data-testid="bible-context-body">
              {context.body}
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
              <span data-testid="bible-context-provenance">
                Based on {context.verses_used} verse{context.verses_used === 1 ? '' : 's'} and{' '}
                {context.notes_used} study note{context.notes_used === 1 ? '' : 's'}.
              </span>
              {onOpenRef && (
                <button
                  type="button"
                  onClick={() => {
                    void trigger('light');
                    onOpenRef(verse.reference);
                  }}
                  className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200"
                >
                  <BookOpen size={12} /> Read the passage
                </button>
              )}
            </div>
          </>
        ) : (
          <p className="text-sm text-slate-400">No context is available for this verse.</p>
        )}
      </div>
    </div>
  );
}
