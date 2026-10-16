import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Check, ChevronRight, Crown, Gamepad2, Library, ListOrdered, Loader2, PenLine, RotateCcw, Star, Timer, Trophy, X, Zap } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleGame, BibleGameInfo, BibleGameMode, BibleGameResult, BibleGameReviewItem } from '../../types/api';
import { pageStyle } from './readingPage';
import { errorText } from '../../lib/errorText';

const ICONS: Record<BibleGameMode, typeof Gamepad2> = {
  daily: Crown,
  verse_race: Zap,
  which_book: BookOpen,
  fill_blank: PenLine,
  next_verse: ListOrdered,
  books: Library,
};

function seconds(ms: number): string {
  const s = Math.round(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

/**
 * Bible games: a daily quiz the whole family shares, a timed race, and
 * practice rounds.
 *
 * The questions come from the installed text and the server keeps the answer
 * key, so a round is scored where it cannot be edited. The verse in a question
 * sits on the reader's own page tone, so it is as easy to read here as it is in
 * the reader.
 */
export default function BibleGames({ page }: { page?: string }) {
  const [game, setGame] = useState<BibleGame | null>(null);
  const [result, setResult] = useState<BibleGameResult | null>(null);
  const [loading, setLoading] = useState<BibleGameMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const start = async (mode: BibleGameMode) => {
    setError(null);
    setLoading(mode);
    try {
      setGame(await api.getBibleGame(mode));
      setResult(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(null);
    }
  };

  if (game && !result) {
    return (
      <GameRound
        game={game}
        page={page}
        onQuit={() => setGame(null)}
        onFinished={(r) => {
          setResult(r);
          void queryClient.invalidateQueries({ queryKey: ['bible-leaderboard'] });
          void queryClient.invalidateQueries({ queryKey: ['bible-daily-game'] });
          void queryClient.invalidateQueries({ queryKey: ['bible-achievements'] });
        }}
      />
    );
  }
  if (game && result) {
    return (
      <GameResults
        game={game}
        result={result}
        page={page}
        onAgain={() => void start(game.mode)}
        onDone={() => {
          setGame(null);
          setResult(null);
        }}
      />
    );
  }
  return <GameLobby onStart={(m) => void start(m)} loading={loading} error={error} page={page} />;
}

function GameLobby({
  onStart,
  loading,
  error,
  page,
}: {
  onStart: (mode: BibleGameMode) => void;
  loading: BibleGameMode | null;
  error: string | null;
  page?: string;
}) {
  const [reviewing, setReviewing] = useState<{ id: number; mine: boolean } | null>(null);
  const { data: catalogue, error: catalogueError } = useQuery({
    queryKey: ['bible-games'],
    queryFn: () => api.getBibleGames(),
    staleTime: 60 * 60 * 1000,
  });
  const { data: daily } = useQuery({
    queryKey: ['bible-daily-game'],
    queryFn: () => api.getBibleGame('daily'),
    retry: false,
  });
  const { data: board } = useQuery({
    queryKey: ['bible-leaderboard'],
    queryFn: () => api.getBibleLeaderboard(),
    retry: false,
    // Family members finish the quiz on their own phones; keep the board
    // current without anyone having to leave and come back.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });

  const modes = catalogue?.modes ?? [];
  const dailyInfo = modes.find((m) => m.daily);
  const others = modes.filter((m) => !m.daily);
  const played = daily?.played_today;

  return (
    <div className="space-y-4" data-testid="bible-games">
      {(error || catalogueError) && (
        <p className="glass-panel rounded-2xl p-4 text-sm text-amber-300/90 leading-relaxed" role="alert">
          {error ?? errorText(catalogueError)}
        </p>
      )}

      {dailyInfo && (
        <section className="rounded-2xl p-5 border border-amber-400/30 bg-gradient-to-br from-amber-500/20 via-orange-500/10 to-slate-900">
          <div className="flex items-start gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-amber-400/20 text-amber-200">
              <Crown size={22} />
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-bold text-white">{dailyInfo.name}</h2>
              <p className="text-sm text-slate-300 leading-relaxed">{dailyInfo.blurb}</p>
              {played && (
                <p className="mt-1 text-sm text-amber-200" data-testid="bible-daily-played">
                  You scored {played.correct}/{played.total} today. Play again for practice.
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => onStart('daily')}
            disabled={loading !== null}
            data-testid="bible-game-start-daily"
            className="mt-4 w-full min-h-12 rounded-xl bg-amber-500 text-slate-950 font-semibold flex items-center justify-center gap-2 hover:bg-amber-400 disabled:opacity-60"
          >
            {loading === 'daily' ? <Loader2 size={16} className="animate-spin" /> : <Gamepad2 size={16} />}
            {played ? 'Practise today’s quiz' : 'Play today’s quiz'}
          </button>
        </section>
      )}

      <section className="glass-panel rounded-2xl p-4 border border-white/10" data-testid="bible-leaderboard">
        <div className="flex items-center gap-2 mb-3">
          <Trophy size={15} className="text-amber-300" />
          <h2 className="text-sm font-bold text-white">Today’s family leaderboard</h2>
        </div>
        {!board?.entries.length ? (
          <p className="text-sm text-slate-400">
            No scores yet today. Be the first. Family members appear here when they share their Bible activity with you.
          </p>
        ) : (
          <ol className="space-y-1.5">
            {board.entries.map((e, i) => (
              <li key={e.username}>
                <button
                  type="button"
                  onClick={() => setReviewing({ id: e.attempt_id, mine: e.you })}
                  aria-label={`See ${e.you ? 'your' : `${e.username}'s`} answers`}
                  data-testid={`bible-leaderboard-${e.username}`}
                  className={`w-full min-h-11 flex items-center gap-3 rounded-xl px-3 py-2 text-left hover:border-amber-400/50 ${e.you ? 'bg-amber-500/10 border border-amber-400/30' : 'bg-white/[0.03] border border-transparent'}`}
                >
                <span className="w-6 text-center text-sm font-bold text-slate-400">{i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : i + 1}</span>
                <span className="flex-1 truncate text-sm text-slate-100 capitalize">{e.you ? `${e.username} (you)` : e.username}</span>
                <span className="text-sm font-semibold text-white tabular-nums">
                  {e.correct}/{e.total}
                </span>
                <span className="w-14 text-right text-xs text-slate-500 tabular-nums">{seconds(e.duration_ms)}</span>
                <ChevronRight size={14} className="text-slate-500 shrink-0" />
                </button>
              </li>
            ))}
          </ol>
        )}
        {board?.entries.length ? <p className="mt-2 text-[11px] text-slate-500">Tap a score to see the answers.</p> : null}
      </section>

      {reviewing && (
        <AttemptReview attemptId={reviewing.id} mine={reviewing.mine} page={page} onClose={() => setReviewing(null)} />
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {others.map((m) => (
          <ModeCard key={m.mode} info={m} loading={loading === m.mode} disabled={loading !== null} onStart={() => onStart(m.mode)} />
        ))}
      </div>
    </div>
  );
}

function ModeCard({ info, loading, disabled, onStart }: { info: BibleGameInfo; loading: boolean; disabled: boolean; onStart: () => void }) {
  const Icon = ICONS[info.mode] ?? Gamepad2;
  return (
    <button
      type="button"
      onClick={onStart}
      disabled={disabled}
      data-testid={`bible-game-start-${info.mode}`}
      className="glass-panel rounded-2xl p-4 border border-white/10 text-left hover:border-purple-400/40 transition-colors disabled:opacity-60 min-h-11"
    >
      <span className="flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-purple-500/15 text-purple-200">
          {loading ? <Loader2 size={17} className="animate-spin" /> : <Icon size={17} />}
        </span>
        <span className="font-semibold text-white">{info.name}</span>
        {info.seconds ? (
          <span className="ml-auto flex items-center gap-1 text-[11px] text-slate-400">
            <Timer size={12} /> {info.seconds}s
          </span>
        ) : (
          <span className="ml-auto text-[11px] text-slate-500">{info.count} questions</span>
        )}
      </span>
      <span className="mt-2 block text-sm text-slate-400 leading-relaxed">{info.blurb}</span>
    </button>
  );
}

function GameRound({
  game,
  page,
  onQuit,
  onFinished,
}: {
  game: BibleGame;
  page?: string;
  onQuit: () => void;
  onFinished: (result: BibleGameResult) => void;
}) {
  const { trigger } = useHaptics();
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<(string | null)[]>(() => game.questions.map(() => null));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const answersRef = useRef(answers);
  const submittedRef = useRef(false);

  const total = game.questions.length;
  const question = game.questions[index];
  const limitMs = game.seconds ? game.seconds * 1000 : null;
  const remainingMs = limitMs ? Math.max(0, limitMs - (now - startedAt)) : null;

  const submit = async (final: (string | null)[]) => {
    if (submittedRef.current) return;
    submittedRef.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const duration = Math.min(Date.now() - startedAt, limitMs ?? Number.MAX_SAFE_INTEGER);
      const result = await api.submitBibleGame(game.mode, { seed: game.seed, version: game.version, answers: final, duration_ms: duration });
      void trigger(result.correct === result.total ? 'success' : 'light');
      onFinished(result);
    } catch (e) {
      submittedRef.current = false;
      setError(errorText(e));
    } finally {
      setSubmitting(false);
    }
  };

  // The race clock: tick while the round runs, and hand in whatever is
  // answered when time runs out.
  useEffect(() => {
    if (!limitMs) return;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      if (Date.now() - startedAt >= limitMs) {
        window.clearInterval(timer);
        void submit(answersRef.current);
      }
    }, 250);
    return () => window.clearInterval(timer);
    // `submit` reads refs, so the interval only depends on the round itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [limitMs, startedAt]);

  const choose = (option: string) => {
    if (submitting) return;
    void trigger('light');
    const next = answers.map((a, i) => (i === index ? option : a));
    answersRef.current = next;
    setAnswers(next);
    if (index + 1 < total) {
      setIndex(index + 1);
    } else {
      void submit(next);
    }
  };

  return (
    <div className="space-y-4" data-testid="bible-game-round">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onQuit}
          aria-label="Leave the game"
          className="min-h-11 min-w-11 flex items-center justify-center rounded-xl glass-button text-slate-300"
        >
          <X size={16} />
        </button>
        <div className="flex-1">
          <div className="flex justify-between text-xs text-slate-400 mb-1">
            <span>{game.name}</span>
            <span className="tabular-nums" data-testid="bible-game-progress">
              {index + 1} / {total}
            </span>
          </div>
          <div className="h-2 rounded-full bg-white/10 overflow-hidden">
            <div className="h-full bg-amber-400 transition-all" style={{ width: `${(index / total) * 100}%` }} />
          </div>
        </div>
        {remainingMs !== null && (
          <span
            className={`flex items-center gap-1 rounded-xl px-2.5 py-1.5 text-sm font-bold tabular-nums ${remainingMs < 10000 ? 'bg-rose-500/20 text-rose-200' : 'bg-white/10 text-white'}`}
            data-testid="bible-game-clock"
          >
            <Timer size={14} /> {Math.ceil(remainingMs / 1000)}
          </span>
        )}
      </div>

      <section className="rounded-2xl p-5 sm:p-6 border border-[var(--page-rule)] shadow-lg" style={pageStyle(page)}>
        <p className="text-sm font-semibold uppercase tracking-wide text-[var(--page-number)]">{question.prompt}</p>
        {question.text && (
          <blockquote className="mt-3 font-serif text-xl leading-relaxed text-[var(--page-ink)]" data-testid="bible-game-verse">
            {question.text}
          </blockquote>
        )}
        {question.reference && <p className="mt-2 text-sm text-[var(--page-muted)]">{question.reference}</p>}
      </section>

      <div className="grid gap-2.5 sm:grid-cols-2" role="group" aria-label="Answers">
        {question.options.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => choose(option)}
            disabled={submitting}
            className="min-h-14 rounded-xl border border-white/15 bg-slate-900 px-4 py-3 text-left text-base font-medium text-white hover:border-amber-400/60 hover:bg-slate-800 active:scale-[0.99] transition disabled:opacity-60"
          >
            {option}
          </button>
        ))}
      </div>

      {submitting && (
        <p className="flex items-center justify-center gap-2 text-sm text-slate-400">
          <Loader2 size={14} className="animate-spin" /> Scoring…
        </p>
      )}
      {error && (
        <div className="glass-panel rounded-2xl p-4 text-sm text-amber-300/90" role="alert">
          {error}
          <button type="button" onClick={() => void submit(answersRef.current)} className="ml-3 min-h-11 px-2 underline">
            Try again
          </button>
        </div>
      )}
    </div>
  );
}

function GameResults({
  game,
  result,
  page,
  onAgain,
  onDone,
}: {
  game: BibleGame;
  result: BibleGameResult;
  page?: string;
  onAgain: () => void;
  onDone: () => void;
}) {
  const perfect = result.correct === result.total;
  const ratio = result.total ? result.correct / result.total : 0;
  const headline = perfect ? 'Perfect!' : ratio >= 0.7 ? 'Well done!' : ratio >= 0.4 ? 'Good effort' : 'Keep reading';
  return (
    <div className="space-y-4" data-testid="bible-game-results">
      <section className="rounded-2xl p-6 text-center border border-amber-400/30 bg-gradient-to-br from-amber-500/20 via-purple-500/10 to-slate-900">
        <p className="text-sm text-slate-300">{game.name}</p>
        <p className="mt-1 text-5xl font-black text-white tabular-nums" data-testid="bible-game-score">
          {result.correct}/{result.total}
        </p>
        <p className="mt-1 text-lg font-semibold text-amber-200">{headline}</p>
        <p className="mt-1 text-xs text-slate-400">{seconds(result.duration_ms)}</p>
        {result.stars.granted > 0 ? (
          <p className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-amber-400/20 px-3 py-1 text-sm text-amber-100">
            <Star size={14} className="fill-amber-300 text-amber-300" /> {result.stars.granted} star{result.stars.granted === 1 ? '' : 's'} banked
          </p>
        ) : (
          <p className="mt-3 text-xs text-slate-400">{result.stars.status}</p>
        )}
        {result.newly_earned.length > 0 && (
          <div className="mt-3 space-y-1">
            {result.newly_earned.map((b) => (
              <p key={b.id} className="text-sm text-purple-200">
                <Trophy size={13} className="inline mr-1" /> New badge: {b.name}
              </p>
            ))}
          </div>
        )}
        <div className="mt-5 grid grid-cols-2 gap-2">
          <button type="button" onClick={onAgain} className="min-h-11 rounded-xl glass-button justify-center text-sm text-white">
            <RotateCcw size={14} /> Play again
          </button>
          <button type="button" onClick={onDone} className="min-h-11 rounded-xl bg-amber-500 text-slate-950 text-sm font-semibold">
            Done
          </button>
        </div>
      </section>

      <ReviewList results={result.results} page={page} />
    </div>
  );
}

/** A game question by question: right or wrong, the answer, and the verse. */
function ReviewList({ results, page, who = 'you' }: { results: BibleGameReviewItem[]; page?: string; who?: string }) {
  const missed = results.filter((r) => !r.correct).length;
  return (
    <section className="rounded-2xl p-4 sm:p-5 border border-[var(--page-rule)]" style={pageStyle(page)} data-testid="bible-game-review">
      <h2 className="text-sm font-bold mb-3 text-[var(--page-ink)]">
        Review{missed ? ` · ${missed} missed` : ' · all right'}
      </h2>
      <ol className="space-y-3">
        {results.map((r) => (
          <li key={r.id} className="border-b border-[var(--page-rule)] pb-3 last:border-0">
            <p className="flex items-start gap-2 text-sm text-[var(--page-ink)]">
              {r.correct ? (
                <Check size={16} className="mt-0.5 shrink-0 text-emerald-500" aria-label="Right" />
              ) : (
                <X size={16} className="mt-0.5 shrink-0 text-rose-500" aria-label="Wrong" />
              )}
              <span>
                {r.prompt} <strong>{r.answer}</strong>
                {!r.correct && (
                  <span className="text-[var(--page-muted)]">
                    {' '}
                    — {who} chose {r.chosen ?? 'nothing'}
                  </span>
                )}
              </span>
            </p>
            {r.text && <p className="mt-1 pl-6 font-serif text-[15px] leading-relaxed text-[var(--page-ink)]">{r.text}</p>}
            {r.reference && <p className="mt-0.5 pl-6 text-xs text-[var(--page-muted)]">{r.reference}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Someone's finished game from the leaderboard, in a sheet over the lobby. */
function AttemptReview({
  attemptId,
  mine,
  page,
  onClose,
}: {
  attemptId: number;
  mine: boolean;
  page?: string;
  onClose: () => void;
}) {
  const { data, error, isLoading } = useQuery({
    queryKey: ['bible-game-review', attemptId],
    queryFn: () => api.getBibleGameReview(attemptId),
    retry: false,
  });
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label="Game answers">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative w-full sm:max-w-xl max-h-[85vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl border border-white/10 bg-slate-950 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="flex-1 text-base font-bold text-white capitalize">
            {data ? `${mine ? 'Your' : `${data.username}'s`} ${data.name} · ${data.correct}/${data.total}` : 'Answers'}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="min-h-11 min-w-11 flex items-center justify-center rounded-xl text-slate-400">
            <X size={16} />
          </button>
        </div>
        {isLoading && <Loader2 size={16} className="animate-spin text-slate-400" />}
        {error && <p className="text-sm text-amber-300" role="alert">{errorText(error)}</p>}
        {data && <ReviewList results={data.results} page={page} who={mine ? 'you' : data.username} />}
      </div>
    </div>
  );
}
