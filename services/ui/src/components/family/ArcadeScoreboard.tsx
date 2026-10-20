import { useQuery } from '@tanstack/react-query';
import { Crown, Gamepad2, Trophy } from 'lucide-react';
import { api } from '../../services/api';
import type { ArcadeScoreboardPlayer } from '../../types/api';

function score(value: number): string {
  return value > 0 ? value.toLocaleString() : 'No score yet';
}

function PlayerRow({ player, rank }: { player: ArcadeScoreboardPlayer; rank: number }) {
  const medal = rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : `${rank}`;
  return (
    <a
      href={player.player_url}
      target="_blank"
      rel="noopener noreferrer"
      className={`flex items-center gap-3 rounded-xl border px-3 py-2 transition hover:border-purple-400/40 ${
        player.you ? 'border-purple-400/50 bg-purple-500/10' : 'border-white/10 bg-white/5'
      }`}
      data-testid={`arcade-score-${player.username}`}
    >
      <span className="w-6 shrink-0 text-center text-base" aria-hidden>{medal}</span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5 truncate text-sm font-semibold text-slate-100">
          {player.display_name}
          {player.you && <span className="rounded-full bg-purple-500/20 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-purple-200">You</span>}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-slate-400">
          <span className="font-mono">{player.initials}</span>
          <span>{player.games_scored} {player.games_scored === 1 ? 'game' : 'games'}</span>
          {player.crowns > 0 && <span className="inline-flex items-center gap-0.5"><Crown size={10} className="text-amber-300" /> {player.crowns}</span>}
          {player.podiums > 0 && <span className="inline-flex items-center gap-0.5"><Trophy size={10} className="text-slate-300" /> {player.podiums}</span>}
        </span>
      </span>
      <span className="shrink-0 text-right">
        <span className="block font-mono text-sm font-semibold text-amber-200">{score(player.best_score)}</span>
        <span className="text-[9px] uppercase tracking-wide text-slate-500">best</span>
      </span>
    </a>
  );
}

/** Family high scores, read live from each member's arcade player card. */
export default function ArcadeScoreboard() {
  const { data } = useQuery({
    queryKey: ['arcade-scoreboard'],
    queryFn: () => api.getArcadeScoreboard(),
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  });

  if (!data?.arcade_available || data.players.length === 0) return null;

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" data-testid="arcade-scoreboard">
      <div className="flex items-center gap-2">
        <Gamepad2 size={16} className="text-amber-300" />
        <h2 className="text-sm font-semibold text-slate-200">Family high scores</h2>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-slate-500">Friendly competition</span>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Scores live in the arcade under each member's initials. Play a game and submit the score; the board updates here.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {data.players.map((player, index) => (
          <PlayerRow key={player.username} player={player} rank={index + 1} />
        ))}
      </div>
    </section>
  );
}
