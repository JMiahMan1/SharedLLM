import { useState } from 'react';
import { BookOpen, BookOpenCheck, Send, Sparkles, X } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleStudyDepth } from '../../types/api';

/**
 * Ask about the passage being read, and see what the answer was built from.
 *
 * The passage is fixed by the reader's own position: the panel names it and the
 * server assembles the verses and study notes for that reference, so a question
 * cannot be answered from somewhere else in the Bible by mistake. Every answer
 * carries how many verses and notes it had in front of it, because "the passage
 * says nothing about this" and "I did not look" are different answers and the
 * reader deserves to tell them apart.
 *
 * A failure is shown as itself -- never as an empty answer -- and the question
 * stays in the box so it can be asked again once the model is back.
 */

interface Turn {
  question: string;
  text: string;
  failed: boolean;
  verses: number;
  notes: number;
  references: string[];
}

const DEPTHS: { id: BibleStudyDepth; label: string }[] = [
  { id: 'adult', label: 'Adult' },
  { id: 'teen', label: 'Teen' },
  { id: 'child', label: 'Child' },
];

interface JarvisStudyThreadProps {
  passage: string;
  version: string;
  edition?: string;
  crossVersion?: boolean;
  onClose: () => void;
  /** Open a reference the answer names in the reader. */
  onOpenRef?: (ref: string) => void;
}

const STARTERS = [
  'What is happening in this passage?',
  'What does this passage say about who God is?',
  'How would I explain this to a child?',
];

const TOUCH = 'min-h-11 pointer-coarse:min-h-11';

export default function JarvisStudyThread({
  passage,
  version,
  edition,
  crossVersion = false,
  onClose,
  onOpenRef,
}: JarvisStudyThreadProps) {
  const { trigger } = useHaptics();
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [asking, setAsking] = useState(false);
  const [depth, setDepth] = useState<BibleStudyDepth>('adult');

  const ask = async (text: string) => {
    const wanted = text.trim();
    if (!wanted || asking) return;
    void trigger('light');
    setAsking(true);
    try {
      const answer = await api.askBibleStudy(passage, wanted, {
        version,
        edition,
        crossVersion,
        depth,
      });
      setTurns((prev) => [
        ...prev,
        {
          question: wanted,
          text: answer.answer,
          failed: false,
          verses: answer.verses_used,
          notes: answer.notes_used,
          references: answer.references ?? [],
        },
      ]);
      setQuestion('');
    } catch (error) {
      // The server's own sentence: an unconfigured gateway, a passage with no
      // text, and a model that could not be reached each have their own fix.
      const message =
        error instanceof Error && error.message
          ? error.message
          : 'The question could not be answered just now.';
      setTurns((prev) => [
        ...prev,
        { question: wanted, text: message, failed: true, verses: 0, notes: 0, references: [] },
      ]);
    } finally {
      setAsking(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={`Ask about ${passage}`}
    >
      <button
        type="button"
        aria-label="Close study help"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        data-testid="bible-ask-scrim"
      />
      <div
        className="relative w-full sm:max-w-lg bg-slate-950/97 backdrop-blur-xl border border-white/10 border-b-0 sm:border-b sm:rounded-2xl p-4 pb-[max(1rem,env(safe-area-inset-bottom))] max-h-[85vh] overflow-y-auto"
        data-testid="bible-ask-panel"
      >
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-xs font-semibold text-sky-300">
              <Sparkles size={14} />
              <span className="truncate" data-testid="bible-ask-ref">
                {passage}
              </span>
            </p>
            <p className="text-[11px] text-slate-500 mt-1 uppercase tracking-wide">
              Answered from this passage and its study notes
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close study help"
            className={`${TOUCH} min-w-11 flex items-center justify-center rounded-xl text-slate-400 hover:text-white`}
          >
            <X size={16} />
          </button>
        </div>

        {turns.length === 0 && (
          <div className="mb-3">
            <p className="text-xs text-slate-400 mb-2">
              Ask anything about the verses on the screen. If they do not answer
              it, Jarvis will say so rather than guess.
            </p>
            <div className="flex flex-wrap gap-1.5" data-testid="bible-ask-starters">
              {STARTERS.map((starter) => (
                <button
                  key={starter}
                  type="button"
                  onClick={() => setQuestion(starter)}
                  className={`${TOUCH} rounded-xl border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-slate-200 text-left`}
                >
                  {starter}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.length > 0 && (
          <ul className="mb-3 space-y-3" data-testid="bible-ask-thread">
            {turns.map((turn, index) => (
              <li key={`${index}-${turn.question}`} className="space-y-1.5">
                <p className="text-xs font-medium text-slate-300">{turn.question}</p>
                <p
                  data-testid={turn.failed ? 'bible-ask-error' : 'bible-ask-answer'}
                  className={`whitespace-pre-wrap text-sm ${
                    turn.failed ? 'text-amber-300' : 'text-slate-100'
                  }`}
                >
                  {turn.text}
                </p>
                {!turn.failed && turn.references.length > 0 && (
                  <div className="flex flex-wrap gap-1.5" data-testid="bible-ask-references">
                    {turn.references.map((ref) => (
                      <button
                        key={ref}
                        type="button"
                        disabled={!onOpenRef}
                        onClick={() => {
                          void trigger('light');
                          onOpenRef?.(ref);
                        }}
                        className={`${TOUCH} rounded-xl border border-sky-400/30 bg-sky-500/10 px-2.5 text-[12px] text-sky-100 flex items-center gap-1`}
                      >
                        <BookOpen size={12} /> {ref}
                      </button>
                    ))}
                  </div>
                )}
                {!turn.failed && (
                  <p
                    className="flex items-center gap-1.5 text-[11px] text-slate-500"
                    data-testid="bible-ask-provenance"
                  >
                    <BookOpenCheck size={12} />
                    Read {turn.verses} verse{turn.verses === 1 ? '' : 's'} and{' '}
                    {turn.notes} note{turn.notes === 1 ? '' : 's'}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void ask(question);
          }}
          className="space-y-2"
        >
          <div className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wide text-slate-500">Pitched for</span>
            <div role="group" aria-label="Who the answer is for" className="flex gap-1 rounded-lg bg-black/40 p-0.5">
              {DEPTHS.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => setDepth(d.id)}
                  aria-pressed={depth === d.id}
                  data-testid={`bible-ask-depth-${d.id}`}
                  className={`min-h-9 rounded-md px-2.5 text-[11px] font-medium ${
                    depth === d.id ? 'bg-sky-500 text-slate-950' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>
          <label className="block text-[11px] uppercase tracking-wide text-slate-500" htmlFor="bible-ask-question">
            Your question
          </label>
          <textarea
            id="bible-ask-question"
            data-testid="bible-ask-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            rows={3}
            placeholder="What does this mean?"
            className={`w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600`}
          />
          <button
            type="submit"
            disabled={asking || !question.trim()}
            data-testid="bible-ask-submit"
            className={`${TOUCH} w-full rounded-xl bg-sky-500/90 px-3 py-2 text-sm font-medium text-slate-950 disabled:opacity-50 flex items-center justify-center gap-2`}
          >
            <Send size={14} />
            {asking ? 'Thinking…' : 'Ask Jarvis'}
          </button>
        </form>
      </div>
    </div>
  );
}
