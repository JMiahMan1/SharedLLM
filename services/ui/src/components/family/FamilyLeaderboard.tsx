import { useQuery } from '@tanstack/react-query';
import { Crown, Medal, Play, Trophy } from 'lucide-react';
import UserAvatar from '../UserAvatar';
import { api } from '../../services/api';
import type { ArcadeScoreboardPlayer, FamilyGameCard } from '../../types/api';

/**
 * Family ranking. Scores from different games are not comparable (160 points
 * in a puzzle is not 160 metres climbed), so the family is ranked by how many
 * games each person leads, then podium finishes, then games played.
 */
function rankPlayers(players: ArcadeScoreboardPlayer[]): ArcadeScoreboardPlayer[] {
  return [...players].sort(
    (a, b) =>
      b.crowns - a.crowns ||
      b.podiums - a.podiums ||
      b.games_scored - a.games_scored ||
      b.submits - a.submits ||
      a.display_name.localeCompare(b.display_name),
  );
}

const PODIUM = [
  { place: 2, height: 'h-16', ring: 'ring-slate-300', block: 'from-slate-300/30 to-slate-400/10', medal: '🥈', avatar: 56 },
  { place: 1, height: 'h-24', ring: 'ring-amber-300', block: 'from-amber-300/40 to-amber-500/10', medal: '🥇', avatar: 72 },
  { place: 3, height: 'h-12', ring: 'ring-orange-400', block: 'from-orange-400/30 to-orange-600/10', medal: '🥉', avatar: 56 },
];

function YouTag() {
  return (
    <span className="rounded-full bg-fuchsia-500/25 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-fuchsia-100">
      You
    </span>
  );
}

function Podium({ top }: { top: ArcadeScoreboardPlayer[] }) {
  return (
    <div className="mt-4 grid grid-cols-3 items-end gap-2" data-testid="family-podium">
      {PODIUM.map(({ place, height, ring, block, medal, avatar }) => {
        const player = top[place - 1];
        if (!player) return <div key={place} />;
        return (
          <div key={place} className="flex min-w-0 flex-col items-center" data-testid={`family-podium-${place}`}>
            <div className="relative">
              {place === 1 && (
                <span className="absolute -top-6 left-1/2 -translate-x-1/2 text-2xl drop-shadow" aria-hidden>
                  👑
                </span>
              )}
              <UserAvatar
                username={player.username}
                avatar={player.avatar}
                name={player.display_name}
                size={avatar}
                className={`ring-4 ${ring} ring-offset-2 ring-offset-slate-900`}
              />
              <span className="absolute -bottom-1 -right-1 text-xl" aria-hidden>
                {medal}
              </span>
            </div>
            <div className="mt-2 max-w-full truncate text-sm font-bold text-slate-100">{player.display_name}</div>
            {player.you && (
              <div className="mt-0.5">
                <YouTag />
              </div>
            )}
            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-300">
              <span className="inline-flex items-center gap-0.5" title="Games led">
                <Crown size={11} className="text-amber-300" /> {player.crowns}
              </span>
              <span className="inline-flex items-center gap-0.5" title="Podium finishes">
                <Medal size={11} className="text-slate-300" /> {player.podiums}
              </span>
            </div>
            <div
              className={`mt-2 flex w-full ${height} items-start justify-center rounded-t-2xl border border-b-0 border-white/10 bg-gradient-to-b ${block} pt-1 text-2xl font-black text-white/80`}
            >
              {place}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PlayerRow({ player, rank }: { player: ArcadeScoreboardPlayer; rank: number }) {
  const played = player.games_scored > 0;
  return (
    <li
      className={`flex min-h-14 items-center gap-3 rounded-2xl border px-3 py-2 ${
        player.you ? 'border-fuchsia-400/40 bg-fuchsia-500/10' : 'border-white/5 bg-white/[0.04]'
      }`}
      data-testid={`family-rank-${player.username}`}
    >
      <span className="w-5 shrink-0 text-center text-sm font-black text-slate-400">{rank}</span>
      <UserAvatar username={player.username} avatar={player.avatar} name={player.display_name} size={36} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-sm font-semibold text-slate-100">{player.display_name}</span>
          {player.you && <YouTag />}
        </span>
        <span className="mt-0.5 block text-[11px] text-slate-400">
          {played ? (
            <>
              👑 {player.crowns} · 🏅 {player.podiums} · {player.games_scored}{' '}
              {player.games_scored === 1 ? 'game' : 'games'}
            </>
          ) : (
            "Hasn't played yet"
          )}
        </span>
      </span>
      {played && (
        <span className="shrink-0 text-right">
          <span className="block font-mono text-sm font-bold text-amber-200">{player.best_score.toLocaleString()}</span>
          <span className="text-[9px] uppercase tracking-wide text-slate-500">top score</span>
        </span>
      )}
    </li>
  );
}

interface Champion {
  game: FamilyGameCard;
  score: number;
  player?: ArcadeScoreboardPlayer;
  initials: string;
}

function ChampionCard({ champion, onPlay }: { champion: Champion; onPlay: () => void }) {
  const { game, player } = champion;
  const from = game.art?.from || '#7c5cff';
  const to = game.art?.to || '#ff7ab6';
  return (
    <div
      className="flex w-44 shrink-0 snap-start flex-col overflow-hidden rounded-2xl border border-white/10 bg-slate-900/60"
      data-testid={`family-champion-${game.slug}`}
    >
      <div
        className="flex h-16 items-center justify-between px-3"
        style={{ backgroundImage: `linear-gradient(135deg, ${from}, ${to})` }}
      >
        <span className="text-3xl drop-shadow" aria-hidden>
          {game.emoji || '🎮'}
        </span>
        <span className="truncate pl-2 text-right text-xs font-bold text-white drop-shadow">{game.title}</span>
      </div>
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex items-center gap-2">
          {player ? (
            <UserAvatar username={player.username} avatar={player.avatar} name={player.display_name} size={28} />
          ) : (
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-white/10 text-[10px] font-bold text-slate-300">
              {champion.initials || '?'}
            </span>
          )}
          <span className="min-w-0">
            <span className="block truncate text-xs font-semibold text-slate-100">
              {player?.display_name ?? champion.initials}
            </span>
            <span className="block font-mono text-sm font-black text-amber-200">{champion.score.toLocaleString()}</span>
          </span>
        </div>
        <button
          type="button"
          onClick={onPlay}
          className="mt-auto inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl bg-white/10 text-xs font-bold text-white transition active:scale-95"
        >
          <Play size={13} fill="currentColor" /> {player?.you ? 'Beat your record' : 'Beat it'}
        </button>
      </div>
    </div>
  );
}

/**
 * The Family Games leaderboard: a podium for the leaders, everyone else in
 * order (including who has not played yet, to invite them in), and each
 * game's current champion with a button to go and beat the record. Players
 * are the signed-in Jarvis family accounts; scores are saved under them.
 */
export default function FamilyLeaderboard({
  games,
  onPlay,
}: {
  games: FamilyGameCard[];
  onPlay: (game: FamilyGameCard) => void;
}) {
  const { data: board } = useQuery({
    queryKey: ['arcade-scoreboard', 'family'],
    queryFn: () => api.getArcadeScoreboard('family'),
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  });
  const { data: highscores } = useQuery({
    queryKey: ['arcade-highscores', 'family'],
    queryFn: () => api.getArcadeHighscores('family'),
    staleTime: 60_000,
    refetchInterval: 120_000,
    retry: false,
  });

  if (!board?.arcade_available) return null;
  const players = rankPlayers(board.players ?? []);
  if (players.length === 0) return null;

  const byUsername = new Map(players.map((p) => [p.username, p]));
  const byInitials = new Map(players.map((p) => [p.initials, p]));
  const shelf = new Map(games.map((g) => [g.slug, g]));
  const champions: Champion[] = (highscores?.games ?? []).flatMap((entry): Champion[] => {
    const game = shelf.get(entry.slug);
    const top = entry.scores?.[0];
    if (!game || !top) return [];
    const player = (top.player_id ? byUsername.get(top.player_id) : undefined) ?? byInitials.get(top.initials);
    return [{ game, score: top.score, initials: top.initials, ...(player ? { player } : {}) }];
  });

  const anyScores = players.some((p) => p.games_scored > 0);
  const podium = anyScores ? players.filter((p) => p.games_scored > 0).slice(0, 3) : [];
  const rest = players.filter((p) => !podium.includes(p));

  return (
    <section
      className="relative overflow-hidden rounded-3xl border border-amber-300/20 bg-gradient-to-b from-indigo-950/80 via-slate-900/80 to-slate-950/80 p-4 shadow-[0_0_40px_rgba(251,191,36,0.08)]"
      data-testid="family-scoreboard"
    >
      <div
        className="pointer-events-none absolute -top-24 left-1/2 h-48 w-72 -translate-x-1/2 rounded-full bg-amber-400/20 blur-3xl"
        aria-hidden
      />
      <div className="relative flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-400/20 text-amber-200">
          <Trophy size={18} />
        </span>
        <div className="min-w-0">
          <h2 className="text-base font-black tracking-tight text-white">Family Leaderboard</h2>
          <p className="text-[11px] text-slate-400">Lead the most games to take the crown.</p>
        </div>
      </div>

      {anyScores ? (
        <Podium top={podium} />
      ) : (
        <div className="relative mt-4 rounded-2xl border border-dashed border-white/15 p-4 text-center" data-testid="family-board-empty">
          <div className="text-3xl" aria-hidden>
            🏆
          </div>
          <p className="mt-1 text-sm font-semibold text-slate-200">No champions yet</p>
          <p className="text-xs text-slate-400">Play a game and your name goes up first.</p>
        </div>
      )}

      {rest.length > 0 && (
        <ol className="relative mt-3 space-y-2" start={podium.length + 1}>
          {rest.map((player, index) => (
            <PlayerRow key={player.username} player={player} rank={podium.length + index + 1} />
          ))}
        </ol>
      )}

      {champions.length > 0 && (
        <div className="relative mt-4">
          <h3 className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-400">Game champions</h3>
          <div
            className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1 [scrollbar-width:none]"
            data-testid="family-champions"
            data-scroll-x
          >
            {champions.map((champion) => (
              <ChampionCard key={champion.game.slug} champion={champion} onPlay={() => onPlay(champion.game)} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
