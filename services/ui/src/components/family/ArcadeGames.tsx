import { useQuery } from '@tanstack/react-query';
import { Gamepad2, Play, Star, Trophy } from 'lucide-react';
import { api } from '../../services/api';
import type { ArcadeGameCard, ArcadeGamesResponse } from '../../types/api';

const SOURCE_LABEL: Record<ArcadeGamesResponse['featured_source'], string> = {
  admin: 'Picks from the family admins',
  rating: 'Top rated by family votes',
  benchmark: 'Newest benchmark winners',
  none: '',
};

function playsLabel(n: number | undefined): string {
  if (!n) return 'No plays yet';
  return n === 1 ? '1 play' : `${n} plays`;
}

function GameCard({ game, playBase }: { game: ArcadeGameCard; playBase: string }) {
  const rating = game.rating ?? { count: 0, average: 0 };
  return (
    <div className="glass-panel p-4 rounded-2xl border border-white/5 flex flex-col gap-2" data-testid={`arcade-game-${game.slug}`}>
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
      <a
        href={`${playBase}/play/${game.slug}`}
        target="_blank"
        rel="noopener noreferrer"
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
  const { data, isLoading } = useQuery<ArcadeGamesResponse>({
    queryKey: ['arcade-games'],
    queryFn: () => api.getArcadeGames(),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });

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
          The game shelf is offline right now{data?.error ? ` — ${data.error}` : ''}. It comes back on its own; nothing
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
      </div>

      {data.count === 0 ? (
        <div className="glass-panel p-4 rounded-2xl border border-white/5" data-testid="arcade-empty">
          <p className="text-xs text-slate-400">No games published yet — make one with Jarvis and it lands here.</p>
        </div>
      ) : (
        <>
          {data.featured.length > 0 && (
            <div>
              <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">Featured</h3>
              <div className="grid gap-3 sm:grid-cols-2" data-testid="arcade-featured">
                {data.featured.map((game) => (
                  <GameCard key={`featured-${game.slug}`} game={game} playBase={data.play_base} />
                ))}
              </div>
            </div>
          )}
          {data.games.length > data.featured.length && (
            <div>
              <h3 className="text-xs uppercase tracking-wider text-slate-500 mb-2">All games</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {data.games.map((game) => (
                  <GameCard key={game.slug} game={game} playBase={data.play_base} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
