import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Gamepad2, Play, Star, Trophy } from 'lucide-react';
import Modal from '../ui/Modal';
import ArcadeHighscores from './ArcadeHighscores';
import ArcadeScoreboard from './ArcadeScoreboard';
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

function GameCard({ game, playBase, onPlay }: { game: ArcadeGameCard; playBase: string; onPlay: () => void }) {
  const rating = game.rating ?? { count: 0, average: 0 };
  const queryClient = useQueryClient();
  const [hovered, setHovered] = useState(0);
  const [selected, setSelected] = useState(0);
  const rate = useMutation({
    mutationFn: (stars: number) => api.rateArcadeGame(game.slug, stars),
    onSuccess: (_data, stars) => {
      setSelected(stars);
      void queryClient.invalidateQueries({ queryKey: ['arcade-games'] });
    },
  });
  const highlighted = hovered || selected;

  return (
    <div className={`glass-panel p-4 rounded-2xl border border-white/5 flex-col gap-2 ${game.kind === 'code' ? 'hidden sm:flex' : 'flex'}`} data-testid={`arcade-game-${game.slug}`}>
      <div className="flex items-start gap-2">
        <span className="font-semibold text-slate-100 leading-tight">{game.title}</span>
        {game.featured && (
          <span className="ml-auto shrink-0 rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-amber-200">
            Featured
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
        {game.category && (
          <span className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 capitalize">{game.category}</span>
        )}
        <span className="inline-flex items-center gap-1" data-testid={`arcade-rating-${game.slug}`}>
          <Star size={11} className={rating.count ? 'text-amber-300' : 'text-slate-500'} />
          {rating.count ? `${rating.average.toFixed(1)} (${rating.count})` : 'No votes'}
        </span>
        <span>{playsLabel(game.plays)}</span>
        {typeof game.top_score === 'number' && (
          <span className="inline-flex items-center gap-1">
            <Trophy size={11} className="text-slate-400" /> best {game.top_score}
          </span>
        )}
      </div>
      <div
        className="flex items-center gap-1"
        data-testid={`arcade-rate-${game.slug}`}
        onMouseLeave={() => setHovered(0)}
      >
        {[1, 2, 3, 4, 5].map((stars) => (
          <button
            key={stars}
            type="button"
            aria-label={`Rate ${game.title} ${stars} stars`}
            title={`${stars} ${stars === 1 ? 'star' : 'stars'}`}
            disabled={rate.isPending}
            onMouseEnter={() => setHovered(stars)}
            onFocus={() => setHovered(stars)}
            onClick={() => rate.mutate(stars)}
            className="rounded p-0.5 transition-colors hover:bg-white/10 disabled:opacity-50"
          >
            <Star
              size={13}
              className={stars <= highlighted ? 'text-amber-300' : 'text-slate-500'}
              fill={stars <= highlighted ? 'currentColor' : 'none'}
            />
          </button>
        ))}
        {rate.isSuccess && <span className="ml-1 text-[10px] text-emerald-300">Thanks!</span>}
        {rate.isError && <span className="ml-1 text-[10px] text-rose-300">Could not save</span>}
      </div>
      <a
        href={`${playBase}/play/${game.slug}`}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(event) => {
          event.preventDefault();
          onPlay();
        }}
        className="glass-button mt-auto min-h-11 px-3 text-sm justify-center text-purple-200 border-purple-400/40"
      >
        <Play size={14} /> Play
      </a>
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
  const [scoreInput, setScoreInput] = useState('');
  const [sort, setSort] = useState<ArcadeSort>('benchmark');
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery<ArcadeGamesResponse>({
    queryKey: ['arcade-games', sort],
    queryFn: () => api.getArcadeGames(sort),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
  const { data: scoreboard } = useQuery({
    queryKey: ['arcade-scoreboard'],
    queryFn: () => api.getArcadeScoreboard(),
    staleTime: 60_000,
    retry: false,
  });
  const me = scoreboard?.players.find((player) => player.you);
  const submitScore = useMutation({
    mutationFn: ({ slug, score }: { slug: string; score: number }) => api.submitArcadeScore(slug, score),
    onSuccess: () => {
      setScoreInput('');
      void queryClient.invalidateQueries({ queryKey: ['arcade-scoreboard'] });
      void queryClient.invalidateQueries({ queryKey: ['arcade-games'] });
    },
  });
  const closePlayer = () => {
    setPlaying(null);
    setScoreInput('');
    submitScore.reset();
  };

  // The embedded arcade page scans the game's own score storage and posts the
  // result here. Prefill the save box instead of sending it silently: the
  // player still confirms the score that belongs to their family account.
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
      const message = event.data as { source?: string; slug?: string; score?: unknown } | null;
      if (message?.source !== 'arcade-score' || message.slug !== playing.slug) return;
      if (typeof message.score !== 'number' || !Number.isFinite(message.score)) return;
      setScoreInput(String(Math.max(0, Math.trunc(message.score))));
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [playing, data?.play_base]);

  if (isLoading) {
    return (
      <div className="glass-panel p-4 rounded-2xl border border-white/5 animate-pulse" data-testid="arcade-loading">
        <div className="h-4 w-40 rounded bg-white/10" />
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
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
      <div className="glass-panel p-4 rounded-2xl border border-white/5">
        <div className="flex items-center gap-2">
          <Gamepad2 size={16} className="text-purple-300" />
          <h2 className="text-sm font-semibold text-slate-200">Arcade</h2>
          <span className="ml-auto text-[10px] uppercase tracking-wider text-slate-500">
            {data.count} {data.count === 1 ? 'game' : 'games'}
          </span>
        </div>
        <p className="text-xs text-slate-400 mt-1">
          Games the family made in Alpaca. {sourceLabel ? `${sourceLabel}.` : ''}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-1" role="group" aria-label="Sort games">
          {SORTS.map((option) => (
            <button
              key={option.id}
              type="button"
              aria-pressed={sort === option.id}
              onClick={() => setSort(option.id)}
              className={`min-h-9 rounded-lg border px-2.5 text-[11px] transition ${
                sort === option.id
                  ? 'border-purple-400/50 bg-purple-500/15 text-purple-100'
                  : 'border-white/10 bg-white/5 text-slate-400 hover:text-slate-200'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <ArcadeScoreboard />
      <ArcadeHighscores />

      {data.count === 0 ? (
        <div className="glass-panel p-4 rounded-2xl border border-white/5" data-testid="arcade-empty">
          <p className="text-xs text-slate-400">No games published yet. Make one with Jarvis and it lands here.</p>
        </div>
      ) : (
        <>
          {data.featured.length > 0 && (
            <div>
              <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Featured</h3>
              <div className="grid gap-3 sm:grid-cols-2" data-testid="arcade-featured">
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
            <div>
              <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">All games</h3>
              <div className="grid gap-3 sm:grid-cols-2">
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
              <input
                type="number"
                inputMode="numeric"
                min={0}
                value={scoreInput}
                onChange={(event) => setScoreInput(event.target.value)}
                placeholder="Your score"
                aria-label="Your score"
                className="w-28 rounded-lg border border-white/10 bg-slate-900 px-2 py-1.5 text-xs text-slate-100 placeholder:text-slate-500"
              />
              <button
                type="button"
                disabled={!scoreInput || submitScore.isPending}
                onClick={() => submitScore.mutate({ slug: playing.slug, score: Number(scoreInput) })}
                className="glass-button min-h-9 px-3 text-xs text-purple-200 border-purple-400/40 disabled:opacity-50"
              >
                Save score
              </button>
              {scoreInput && !submitScore.isSuccess && (
                <span className="text-[10px] text-slate-500">Check the score, then save it to the family board.</span>
              )}
              {submitScore.isSuccess && <span className="text-[10px] text-emerald-300">Saved to the family board</span>}
              {submitScore.isError && <span className="text-[10px] text-rose-300">Could not save</span>}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
