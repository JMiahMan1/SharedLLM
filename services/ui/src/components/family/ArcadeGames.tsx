import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Gamepad2, Play, Star, Trophy } from 'lucide-react';
import Modal from '../ui/Modal';
import ArcadeHighscores from './ArcadeHighscores';
import ArcadeScoreboard from './ArcadeScoreboard';
import GameArtwork from './GameArtwork';
import { api } from '../../services/api';
import type { ArcadeGameCard, ArcadeGamesResponse, ArcadeSort } from '../../types/api';

const SOURCE_LABEL: Record<ArcadeGamesResponse['featured_source'], string> = {
  admin: 'Picks from the family admins',
  rating: 'Top rated by family votes',
  benchmark: 'Newest benchmark winners',
  none: '',
};

const SORTS: Array<{ id: ArcadeSort; label: string }> = [
  { id: 'benchmark', label: 'Alpaca score' },
  { id: 'family', label: 'Family rating' },
  { id: 'plays', label: 'Most played' },
];

function playsLabel(n: number | undefined): string {
  if (!n) return 'No plays yet';
  return n === 1 ? '1 play' : `${n} plays`;
}

const CATEGORY_EMOJI: Record<string, string> = {
  arcade: '👾',
  classic: '🕹️',
  puzzle: '🧩',
  action: '⚡',
  strategy: '♟️',
};

function GameCard({ game, playBase, onPlay }: { game: ArcadeGameCard; playBase: string; onPlay: () => void }) {
  const rating = game.rating ?? { count: 0, average: 0 };
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState(0);
  const rate = useMutation({
    mutationFn: (stars: number) => api.rateArcadeGame(game.slug, stars),
    onSuccess: (_data, stars) => {
      setSelected(stars);
      void queryClient.invalidateQueries({ queryKey: ['arcade-games'] });
    },
  });
  const highlighted = selected || Math.round(rating.average || 0);
  const emoji = CATEGORY_EMOJI[(game.category || '').toLowerCase()] || '🕹️';

  return (
    <div
      className="group glass-panel flex flex-col overflow-hidden rounded-2xl border border-white/10 transition-all hover:border-purple-400/30 hover:shadow-lg"
      data-testid={`arcade-game-${game.slug}`}
    >
      <div className="relative h-28 w-full overflow-hidden">
        <GameArtwork
          slug={game.slug}
          emoji={emoji}
          category={game.category}
          variant="banner"
          className="h-full w-full"
        />
        <div className="absolute left-3 right-3 top-2.5 z-20 flex items-center justify-between gap-2">
          {game.category ? (
            <span className="rounded-full border border-white/20 bg-black/45 px-2.5 py-0.5 text-[10px] font-semibold capitalize text-slate-100 backdrop-blur-md">
              {game.category}
            </span>
          ) : <span />}
          <div className="flex items-center gap-1.5">
            {game.kind === 'code' && (
              <span className="rounded-full border border-sky-400/40 bg-sky-950/70 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-sky-200 backdrop-blur-md">
                Desktop
              </span>
            )}
            {game.featured && (
              <span className="rounded-full border border-amber-400/50 bg-amber-950/70 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-200 backdrop-blur-md">
                Featured
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-2.5 p-4">
        <div className="flex items-start justify-between gap-2">
          <span className="min-w-0 truncate text-sm font-bold leading-tight text-white">{game.title}</span>
          {typeof game.top_score === 'number' && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-amber-400/25 bg-amber-400/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-amber-200">
              <Trophy size={10} /> best {game.top_score}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
          <span className="inline-flex items-center gap-1" data-testid={`arcade-rating-${game.slug}`}>
            <Star size={11} className={rating.count ? 'text-amber-300' : 'text-slate-500'} fill={rating.count ? 'currentColor' : 'none'} />
            {rating.count ? `${rating.average.toFixed(1)} (${rating.count})` : 'No votes'}
          </span>
          <span>{playsLabel(game.plays)}</span>
        </div>
        <div
          className="flex flex-wrap items-center gap-0.5 border-t border-white/5 pt-1.5"
          data-testid={`arcade-rate-${game.slug}`}
        >
          {[1, 2, 3, 4, 5].map((stars) => (
            <button
              key={stars}
              type="button"
              aria-label={`Rate ${game.title} ${stars} stars`}
              title={`${stars} ${stars === 1 ? 'star' : 'stars'}`}
              disabled={rate.isPending}
              onClick={() => rate.mutate(stars)}
              className="flex h-11 w-9 items-center justify-center rounded-xl transition-colors hover:bg-white/10 active:scale-90 disabled:opacity-50"
            >
              <Star
                size={16}
                className={stars <= highlighted ? 'text-amber-300' : 'text-slate-500'}
                fill={stars <= highlighted ? 'currentColor' : 'none'}
              />
            </button>
          ))}
          {rate.isSuccess && <span className="ml-1 text-[11px] font-medium text-emerald-300">Thanks!</span>}
          {rate.isError && <span className="ml-1 text-[11px] text-rose-300">Could not save</span>}
        </div>
        <a
          href={`${playBase}/play/${game.slug}`}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => {
            event.preventDefault();
            onPlay();
          }}
          className="glass-button mt-auto inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border-purple-400/40 bg-purple-500/15 px-3 text-sm font-semibold text-purple-100 hover:bg-purple-500/25"
        >
          <Play size={14} fill="currentColor" /> Play
        </a>
      </div>
    </div>
  );
}

/**
 * Live arcade shelf: featured games first (admin picks, else top rated) with
 * the full published list below. The gateway always answers 200, so an
 * unreachable arcade renders an honest offline card instead of an error.
 */
export default function ArcadeGames() {
  const [playing, setPlaying] = useState<ArcadeGameCard | null>(null);
  const [sort, setSort] = useState<ArcadeSort>('benchmark');
  const submittedCaptures = useRef(new Set<string>());
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery<ArcadeGamesResponse>({
    queryKey: ['arcade-games', sort],
    queryFn: () => api.getArcadeGames(sort),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
  const { data: scoreboard } = useQuery({
    queryKey: ['arcade-scoreboard', 'arcade'],
    queryFn: () => api.getArcadeScoreboard(),
    staleTime: 60_000,
    retry: false,
  });
  const me = scoreboard?.players.find((player) => player.you);
  const submitScore = useMutation({
    mutationFn: ({ slug, score, captureId }: { slug: string; score: number; captureId: string }) =>
      api.submitArcadeScore(slug, score, captureId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['arcade-scoreboard'] });
      void queryClient.invalidateQueries({ queryKey: ['arcade-games'] });
      void queryClient.invalidateQueries({ queryKey: ['arcade-highscores'] });
    },
  });
  const closePlayer = () => {
    setPlaying(null);
    submittedCaptures.current.clear();
    submitScore.reset();
  };

  // The embedded arcade page captures the score from the game itself and posts
  // it here with a one-time capture id. Submit it through the authenticated
  // gateway so the score belongs to the signed-in family member, not a typed
  // callsign or a score the player invented.
  useEffect(() => {
    if (!playing || !data?.play_base) return;
    let origin: string;
    try {
      origin = new URL(data.play_base).origin;
    } catch {
      return;
    }
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== origin) return;
      const message = event.data as {
        source?: string;
        slug?: string;
        score?: unknown;
        capture_id?: unknown;
      } | null;
      if (message?.source !== 'arcade-score' || message.slug !== playing.slug) return;
      if (typeof message.score !== 'number' || !Number.isFinite(message.score)) return;
      if (typeof message.capture_id !== 'string' || !message.capture_id) return;
      const score = Math.max(0, Math.trunc(message.score));
      if (submittedCaptures.current.has(message.capture_id)) return;
      submittedCaptures.current.add(message.capture_id);
      submitScore.mutate({ slug: playing.slug, score, captureId: message.capture_id });
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [playing, data?.play_base, submitScore]);

  if (isLoading) {
    return (
      <div className="glass-panel p-4 rounded-2xl border border-white/5 animate-pulse" data-testid="arcade-loading">
        <div className="h-4 w-40 rounded bg-white/10" />
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="h-24 rounded-xl bg-white/5" />
          <div className="h-24 rounded-xl bg-white/5" />
        </div>
      </div>
    );
  }

  if (!data || !data.arcade_available) {
    return (
      <div className="glass-panel p-4 rounded-2xl border border-white/5" data-testid="arcade-offline">
        <div className="flex items-center gap-2">
          <Gamepad2 size={16} className="text-slate-400" />
          <span className="font-semibold text-slate-200">Arcade</span>
        </div>
        <p className="text-xs text-slate-400 mt-1">
          The game shelf is offline right now{data?.error ? ` (${data.error})` : ''}. It comes back on its own; nothing
          is lost.
        </p>
      </div>
    );
  }

  const sourceLabel = SOURCE_LABEL[data.featured_source];

  return (
    <div className="space-y-4" data-testid="arcade-shelf">
      <div className="glass-panel relative overflow-hidden rounded-2xl border border-white/10 p-4 sm:p-5">
        <div className="pointer-events-none absolute -right-12 -top-12 h-32 w-32 rounded-full bg-purple-500/10 blur-3xl" aria-hidden />
        <div className="relative flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-purple-400/30 bg-purple-500/15 text-purple-300">
              <Gamepad2 size={18} />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-bold text-white">Arcade</h2>
                <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-slate-300">
                  {data.count} {data.count === 1 ? 'game' : 'games'}
                </span>
              </div>
              <p className="mt-0.5 text-xs leading-relaxed text-slate-400">
                Games the family made in Alpaca. {sourceLabel ? `${sourceLabel}.` : ''} Desktop games open in the live
                sandbox and can be played here too.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Sort games">
            {SORTS.map((option) => (
              <button
                key={option.id}
                type="button"
                aria-pressed={sort === option.id}
                onClick={() => setSort(option.id)}
                className={`min-h-11 rounded-xl border px-3 text-xs font-semibold transition ${
                  sort === option.id
                    ? 'border-purple-400/50 bg-purple-500/20 text-purple-100'
                    : 'border-white/10 bg-white/5 text-slate-400 hover:text-slate-200'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <ArcadeScoreboard />
      <ArcadeHighscores />

      {data.count === 0 ? (
        <div className="glass-panel rounded-2xl border border-white/10 p-5 text-center" data-testid="arcade-empty">
          <p className="text-xs text-slate-400">No games published yet. Make one with Jarvis and it lands here.</p>
        </div>
      ) : (
        <>
          {data.featured.length > 0 && (
            <div className="space-y-2.5">
              <h3 className="px-1 text-xs font-bold uppercase tracking-wider text-slate-400">Featured</h3>
              <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-3" data-testid="arcade-featured">
                {data.featured.map((game) => (
                  <GameCard
                    key={`featured-${game.slug}`}
                    game={game}
                    playBase={data.play_base}
                    onPlay={() => setPlaying(game)}
                  />
                ))}
              </div>
            </div>
          )}
          {data.games.length > data.featured.length && (
            <div className="space-y-2.5">
              <h3 className="px-1 text-xs font-bold uppercase tracking-wider text-slate-400">All games</h3>
              <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-3">
                {data.games.map((game) => (
                  <GameCard
                    key={game.slug}
                    game={game}
                    playBase={data.play_base}
                    onPlay={() => setPlaying(game)}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <Modal isOpen={playing !== null} onClose={closePlayer} title={playing?.title} fullScreen>
        {playing && (
          <div className="flex min-h-0 flex-1 flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2 px-1">
              <p className="text-xs text-slate-400">Play right here. The game runs on the arcade host.</p>
              <a
                href={`${data.play_base}/play/${playing.slug}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 items-center gap-1 text-xs text-purple-200 hover:text-purple-100"
              >
                <ExternalLink size={13} /> Open in a new tab
              </a>
            </div>
            <iframe
              src={`${data.play_base}/play/${playing.slug}?embed=1&player=${encodeURIComponent(me?.initials ?? 'YOU')}`}
              title={`${playing.title} game`}
              data-testid="arcade-player-frame"
              className="min-h-0 w-full flex-1 rounded-none border-0 bg-black sm:rounded-xl"
              allow="autoplay; fullscreen; gamepad"
              allowFullScreen
            />
            <div className="flex flex-wrap items-center gap-2 border-t border-white/10 px-1 pt-2 pb-[env(safe-area-inset-bottom)]">
              <span className="text-[11px] text-slate-400">
                Playing as <b className="font-mono text-slate-200">{me?.initials ?? 'YOU'}</b>
              </span>
              <span className="text-[10px] text-slate-500">
                Scores save automatically when you finish.
              </span>
              {submitScore.isPending && (
                <span className="text-[10px] text-sky-300">Saving captured score…</span>
              )}
              {submitScore.isSuccess && (
                <span className="text-[10px] text-emerald-300">
                  Saved to the family board{typeof submitScore.data?.rank === 'number' ? ` (#${submitScore.data.rank})` : ''}
                </span>
              )}
              {submitScore.isError && <span className="text-[10px] text-rose-300">Could not save the captured score</span>}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
