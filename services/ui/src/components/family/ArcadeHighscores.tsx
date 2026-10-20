import { useQuery } from '@tanstack/react-query';
import { Trophy } from 'lucide-react';
import { api } from '../../services/api';

function score(value: number): string {
  return value.toLocaleString();
}

/** Top scores for each curated game, straight from the arcade's durable ledger. */
export default function ArcadeHighscores() {
  const { data } = useQuery({
    queryKey: ['arcade-highscores'],
    queryFn: () => api.getArcadeHighscores(),
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  });

  const games = (data?.games ?? []).filter((game) => game.scores.length > 0);
  if (!data?.arcade_available || games.length === 0) return null;

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" data-testid="arcade-highscores">
      <div className="flex items-center gap-2">
        <Trophy size={16} className="text-amber-300" />
        <h2 className="text-sm font-semibold text-slate-200">High scores by game</h2>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-slate-500">Top 3</span>
      </div>
      <div className="mt-3 grid max-h-96 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
        {games.map((game) => (
          <div
            key={game.slug}
            className="rounded-xl border border-white/10 bg-white/5 px-3 py-2"
            data-testid={`arcade-highscore-${game.slug}`}
          >
            <div className="truncate text-xs font-semibold text-slate-100">{game.title}</div>
            <ol className="mt-1 space-y-0.5">
              {game.scores.slice(0, 3).map((entry, index) => (
                <li key={`${entry.initials}-${entry.at ?? index}`} className="flex items-center gap-2 text-[11px]">
                  <span className="w-4 text-center text-slate-500" aria-hidden>
                    {index === 0 ? '🥇' : index === 1 ? '🥈' : '🥉'}
                  </span>
                  <span className="font-mono text-slate-300">{entry.initials}</span>
                  <span className="ml-auto font-mono font-semibold text-amber-200">{score(entry.score)}</span>
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </section>
  );
}
