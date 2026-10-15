import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Brain, Check, ChevronRight, Eye, Loader2, Plus, Trash2, Trophy, X } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleMemoryVerse, BibleRecallResult } from '../../types/api';
import { pageStyle } from './readingPage';
import { errorText } from '../../lib/errorText';

const STAGE_LABEL: Record<string, string> = {
  read: 'Read and type',
  cloze: 'Fill the gaps',
  first_letters: 'First letters',
  recall: 'From memory',
};

function daysUntil(day: string): number {
  const ms = new Date(`${day}T00:00:00`).getTime() - new Date(new Date().toDateString()).getTime();
  return Math.round(ms / 86_400_000);
}

function LevelDots({ level }: { level: number }) {
  return (
    <span className="flex gap-0.5" aria-label={`Level ${level} of 5`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} className={`h-1.5 w-3 rounded-full ${n <= level ? 'bg-fuchsia-400' : 'bg-white/15'}`} />
      ))}
    </span>
  );
}

/**
 * Scripture memory: verses climb a ladder from reading to full recall, and
 * come back for review on a widening schedule. Every attempt is graded on the
 * server against the reader's own translation.
 */
export default function BibleMemorize({ page }: { page?: string }) {
  const queryClient = useQueryClient();
  const { trigger } = useHaptics();
  const [practising, setPractising] = useState<BibleMemoryVerse[] | null>(null);
  const [ref, setRef] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({ queryKey: ['bible-memory'], queryFn: () => api.getBibleMemory() });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['bible-memory'] });

  const add = async (body: { ref?: string; set_id?: string }, key: string) => {
    setBusy(key);
    setStatus(null);
    try {
      const result = await api.addBibleMemory(body);
      queryClient.setQueryData(['bible-memory'], result);
      void trigger('success');
      const added = result.added?.length ?? 0;
      setStatus(
        added
          ? `Added ${added === 1 ? result.added?.[0] : `${added} verses`}.${result.skipped?.length ? ` ${result.skipped.length} not in this translation.` : ''}`
          : 'Those verses are already on your list.',
      );
      if (body.ref) setRef('');
    } catch (e) {
      setStatus(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const startPractice = async () => {
    setBusy('practice');
    try {
      const due = await api.getBibleMemoryDue();
      setPractising(due.due);
    } catch (e) {
      setStatus(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  if (practising) {
    return (
      <PracticeSession
        verses={practising}
        page={page}
        onDone={() => {
          setPractising(null);
          void refresh();
          void queryClient.invalidateQueries({ queryKey: ['bible-achievements'] });
        }}
      />
    );
  }

  if (isLoading) {
    return (
      <div className="glass-panel rounded-2xl p-4 space-y-2">
        <div className="skeleton h-4 w-1/3 rounded" />
        <div className="skeleton h-4 w-full rounded" />
      </div>
    );
  }
  if (error) {
    return <p className="glass-panel rounded-2xl p-4 text-sm text-amber-300/90" role="alert">{errorText(error)}</p>;
  }

  const verses = data?.verses ?? [];
  const due = data?.due_count ?? 0;

  return (
    <div className="space-y-4" data-testid="bible-memorize">
      <section className="rounded-2xl p-5 border border-fuchsia-400/30 bg-gradient-to-br from-fuchsia-500/20 via-purple-500/10 to-slate-900">
        <div className="flex items-start gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-fuchsia-400/20 text-fuchsia-200">
            <Brain size={22} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-bold text-white">Hide it in your heart</h2>
            <p className="text-sm text-slate-300 leading-relaxed">
              {verses.length === 0
                ? 'Pick a set below, or add any verse — from here or from the reader.'
                : `${verses.length} verse${verses.length === 1 ? '' : 's'} on your list, ${data?.mastered ?? 0} mastered.`}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void startPractice()}
          disabled={due === 0 || busy !== null}
          data-testid="bible-memory-practice"
          className="mt-4 w-full min-h-12 rounded-xl bg-fuchsia-500 text-white font-semibold flex items-center justify-center gap-2 hover:bg-fuchsia-400 disabled:opacity-50"
        >
          {busy === 'practice' ? <Loader2 size={16} className="animate-spin" /> : <Brain size={16} />}
          {due ? `Practise ${due} due verse${due === 1 ? '' : 's'}` : 'Nothing due — come back tomorrow'}
        </button>
      </section>

      <form
        className="glass-panel rounded-2xl p-4 border border-white/10 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (ref.trim()) void add({ ref: ref.trim() }, 'ref');
        }}
      >
        <input
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder="Add a verse, e.g. Psalm 119:11"
          aria-label="Verse to memorise"
          className="min-h-11 flex-1 rounded-xl border border-white/10 bg-black/40 px-3 text-sm text-white placeholder-slate-500 outline-none focus:border-fuchsia-400"
        />
        <button
          type="submit"
          disabled={!ref.trim() || busy !== null}
          className="min-h-11 rounded-xl bg-white/10 px-4 text-sm text-white flex items-center gap-1.5 disabled:opacity-40"
        >
          {busy === 'ref' ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add
        </button>
      </form>
      {status && (
        <p className="text-sm text-slate-300 px-1" role="status">
          {status}
        </p>
      )}

      {verses.length > 0 && (
        <section className="glass-panel rounded-2xl p-4 border border-white/10">
          <h2 className="text-sm font-bold text-white mb-2">Your verses</h2>
          <ul className="divide-y divide-white/5">
            {verses.map((v) => {
              const days = daysUntil(v.due_on);
              return (
                <li key={v.id} className="flex items-center gap-3 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-slate-100">{v.ref}</p>
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                      <LevelDots level={v.level} />
                      <span>{v.mastered ? 'Mastered' : STAGE_LABEL[v.stage]}</span>
                      <span>· {days <= 0 ? <span className="text-fuchsia-300">due now</span> : `next in ${days} day${days === 1 ? '' : 's'}`}</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    aria-label={`Stop memorising ${v.ref}`}
                    onClick={async () => {
                      queryClient.setQueryData(['bible-memory'], await api.removeBibleMemory(v.id));
                    }}
                    className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-500 hover:text-rose-300"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section>
        <h2 className="text-sm font-bold text-white mb-2 px-1">Memory sets</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {(data?.sets ?? []).map((s) => {
            const complete = s.added >= s.count;
            return (
              <div key={s.id} className="glass-panel rounded-2xl p-4 border border-white/10">
                <p className="font-semibold text-white">{s.name}</p>
                <p className="mt-1 text-sm text-slate-400 leading-relaxed">{s.blurb}</p>
                <div className="mt-3 flex items-center justify-between">
                  <span className="text-xs text-slate-500">
                    {s.added ? `${s.added} of ${s.count} added` : `${s.count} verses`}
                  </span>
                  <button
                    type="button"
                    onClick={() => void add({ set_id: s.id }, s.id)}
                    disabled={complete || busy !== null}
                    data-testid={`bible-memory-set-${s.id}`}
                    className="min-h-11 rounded-xl bg-white/10 px-3 text-sm text-white flex items-center gap-1.5 disabled:opacity-40"
                  >
                    {busy === s.id ? <Loader2 size={14} className="animate-spin" /> : complete ? <Check size={14} /> : <Plus size={14} />}
                    {complete ? 'Added' : 'Add set'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function PracticeSession({ verses, page, onDone }: { verses: BibleMemoryVerse[]; page?: string; onDone: () => void }) {
  const [index, setIndex] = useState(0);
  const [passed, setPassed] = useState(0);
  const [earned, setEarned] = useState<string[]>([]);

  if (index >= verses.length) {
    return (
      <section className="rounded-2xl p-6 text-center border border-fuchsia-400/30 bg-gradient-to-br from-fuchsia-500/20 to-slate-900" data-testid="bible-memory-done">
        <Brain size={28} className="mx-auto text-fuchsia-200" />
        <p className="mt-2 text-2xl font-bold text-white">
          {passed} of {verses.length} recalled
        </p>
        <p className="mt-1 text-sm text-slate-300">Each verse comes back when it is due, a little further apart each time.</p>
        {earned.map((name) => (
          <p key={name} className="mt-2 text-sm text-purple-200">
            <Trophy size={13} className="inline mr-1" /> New badge: {name}
          </p>
        ))}
        <button type="button" onClick={onDone} className="mt-5 min-h-11 w-full rounded-xl bg-fuchsia-500 font-semibold text-white">
          Done
        </button>
      </section>
    );
  }

  const verse = verses[index];
  return (
    <RecallCard
      key={verse.id}
      verse={verse}
      position={`${index + 1} / ${verses.length}`}
      page={page}
      onQuit={onDone}
      onNext={(result) => {
        if (result.passed) setPassed((n) => n + 1);
        setEarned((e) => [...e, ...result.newly_earned.map((b) => b.name)]);
        setIndex((i) => i + 1);
      }}
    />
  );
}

function RecallCard({
  verse,
  position,
  page,
  onQuit,
  onNext,
}: {
  verse: BibleMemoryVerse;
  position: string;
  page?: string;
  onQuit: () => void;
  onNext: (result: BibleRecallResult) => void;
}) {
  const { trigger } = useHaptics();
  const [typed, setTyped] = useState('');
  const [gaps, setGaps] = useState<Record<number, string>>({});
  const [peek, setPeek] = useState(false);
  const [result, setResult] = useState<BibleRecallResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stage = verse.stage;

  const attempt = (): string => {
    if (stage === 'cloze' && verse.cloze) {
      return verse.cloze.map((w, i) => (w.hidden ? gaps[i] ?? '' : w.word)).join(' ');
    }
    return typed;
  };

  const check = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.recallBibleMemory(verse.id, attempt());
      setResult(r);
      void trigger(r.passed ? 'success' : 'light');
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const next = result ? daysUntil(result.verse.due_on) : 0;

  return (
    <div className="space-y-4" data-testid="bible-recall">
      <div className="flex items-center gap-3">
        <button type="button" onClick={onQuit} aria-label="Stop practising" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl glass-button text-slate-300">
          <X size={16} />
        </button>
        <div className="flex-1">
          <p className="text-xs text-slate-400">{STAGE_LABEL[stage]}</p>
          <LevelDots level={verse.level} />
        </div>
        <span className="text-xs text-slate-400 tabular-nums">{position}</span>
      </div>

      <section className="rounded-2xl p-5 sm:p-6 border border-[var(--page-rule)] shadow-lg space-y-3" style={pageStyle(page)}>
        <p className="text-sm font-semibold uppercase tracking-wide text-[var(--page-number)]" data-testid="bible-recall-ref">
          {verse.ref}
        </p>

        {result ? (
          <p className="font-serif text-xl leading-relaxed text-[var(--page-ink)]" data-testid="bible-recall-marked">
            {result.words.map((w, i) => (
              <span key={i} className={w.ok ? '' : 'rounded bg-rose-500/25 px-0.5 underline decoration-rose-500 decoration-2'}>
                {w.word}{' '}
              </span>
            ))}
          </p>
        ) : stage === 'read' ? (
          <p className="font-serif text-xl leading-relaxed text-[var(--page-ink)]">{verse.text}</p>
        ) : stage === 'cloze' && verse.cloze ? (
          <p className="font-serif text-xl leading-loose text-[var(--page-ink)]">
            {verse.cloze.map((w, i) =>
              w.hidden ? (
                <input
                  key={i}
                  aria-label={`Missing word ${i + 1}`}
                  value={gaps[i] ?? ''}
                  onChange={(e) => setGaps((g) => ({ ...g, [i]: e.target.value }))}
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  className="mx-0.5 inline-block rounded border-b-2 border-[var(--page-number)] bg-transparent px-1 text-center font-serif text-[var(--page-ink)] outline-none"
                  style={{ width: `${Math.max(3, w.word.length) + 1}ch` }}
                />
              ) : (
                <span key={i}>{w.word} </span>
              ),
            )}
          </p>
        ) : stage === 'first_letters' || peek ? (
          <p className="font-mono text-lg leading-relaxed tracking-wide text-[var(--page-muted)]">{verse.first_letters}</p>
        ) : (
          <p className="text-sm italic text-[var(--page-muted)]">Say it, then type it from memory.</p>
        )}
      </section>

      {!result && stage !== 'cloze' && (
        <textarea
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          rows={4}
          autoFocus
          autoCapitalize="sentences"
          aria-label="Type the verse"
          placeholder={stage === 'read' ? 'Type it as you read it…' : 'Type the verse…'}
          className="w-full rounded-2xl border border-white/10 bg-slate-900 p-4 font-serif text-lg text-white placeholder-slate-500 outline-none focus:border-fuchsia-400"
        />
      )}

      {error && <p className="text-sm text-amber-300" role="alert">{error}</p>}

      {result ? (
        <div className="space-y-3">
          <p className={`text-center text-lg font-bold ${result.passed ? 'text-emerald-300' : 'text-amber-200'}`} data-testid="bible-recall-verdict">
            {result.passed ? 'Got it!' : 'Not quite — it will come back tomorrow.'} {Math.round(result.score * 100)}%
          </p>
          <p className="text-center text-sm text-slate-400">
            {result.verse.mastered
              ? `Mastered. Next review in ${next} days.`
              : `Next: ${STAGE_LABEL[result.verse.stage].toLowerCase()} in ${next} day${next === 1 ? '' : 's'}.`}
          </p>
          <button type="button" onClick={() => onNext(result)} className="min-h-12 w-full rounded-xl bg-fuchsia-500 font-semibold text-white flex items-center justify-center gap-2">
            Next <ChevronRight size={16} />
          </button>
        </div>
      ) : (
        <div className="flex gap-2">
          {stage === 'recall' && !peek && (
            <button type="button" onClick={() => setPeek(true)} className="min-h-12 rounded-xl glass-button px-4 text-sm text-slate-200">
              <Eye size={14} /> Hint
            </button>
          )}
          <button
            type="button"
            onClick={() => void check()}
            disabled={busy || !attempt().trim()}
            data-testid="bible-recall-check"
            className="min-h-12 flex-1 rounded-xl bg-fuchsia-500 font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />} Check
          </button>
        </div>
      )}
    </div>
  );
}
