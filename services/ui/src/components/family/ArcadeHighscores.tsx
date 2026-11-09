import { useQuery } from '@tanstack/react-query';
import { Trophy } from 'lucide-react';
import { api } from '../../services/api';
import type { ArcadeCollection } from '../../types/api';

function score(value: number): string {
  return value.toLocaleString();
}

/** Top scores for each curated game, straight from the arcade's durable ledger. */
export default function ArcadeHighscores({ collection = 'arcade' }: { collection?: ArcadeCollection } = {}) {
  const { data } = useQuery({
    queryKey: ['arcade-highscores', collection],
    queryFn: () => api.getArcadeHighscores(collection),
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  });

  const games = (data?.games ?? []).filter((game) => game.scores.length > 0);
  if (!data?.arcade_available || games.length === 0) return null;

  return (
    <section className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5" data-testid={collection === 'family' ? 'family-highscores' : 'arcade-highscores'}>
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-amber-400/25 bg-amber-400/10 text-amber-300">
          <Trophy size={15} />
        </div>
        <h2 className="text-sm font-semibold text-white">High scores by game</h2>
        <span className="ml-auto rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Top 3
        </span>
      </div>
      <div className="mt-3 grid max-h-96 grid-cols-1 gap-2.5 overflow-y-auto pr-1 sm:grid-cols-2 xl:grid-cols-3">
        {games.map((game) => (
          <div
            key={game.slug}
            className="rounded-xl border border-white/10 bg-white/[0.04] p-3"
            data-testid={`arcade-highscore-${game.slug}`}
          >
            <div className="truncate border-b border-white/5 pb-1.5 text-xs font-bold text-slate-100">{game.title}</div>
            <ol className="mt-2 space-y-1">
              {game.scores.slice(0, 3).map((entry, index) => (
                <li key={`${entry.initials}-${entry.at ?? index}`} className="flex items-center gap-2 text-[11px]">
                  <span className="w-4 text-center text-slate-500" aria-hidden>
                    {index === 0 ? '🥇' : index === 1 ? '🥈' : '🥉'}
                  </span>
                  <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-slate-200">{entry.initials}</span>
                  <span className="ml-auto font-mono font-bold text-amber-200">{score(entry.score)}</span>
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </section>
  );
}
