import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, ChevronDown, Eye, EyeOff, FlaskConical, PartyPopper, Play, Sparkles, Square, Star, Trophy, Tv } from 'lucide-react';
import FamilyLeaderboard from './FamilyLeaderboard';
import GameArtwork from './GameArtwork';
import GamePlayer from './GamePlayer';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { FamilyGameCard, FamilyGameState, FamilyGamesResponse, PartyRoomSummary } from '../../types/api';

const FAMILY_KEY = ['family-games'];

const STATE_BADGE: Record<FamilyGameState, { label: string; className: string }> = {
  testing: { label: 'Testing', className: 'border-sky-400/40 bg-sky-400/10 text-sky-200' },
  published: { label: 'Published', className: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200' },
  unpublished: { label: 'Unpublished', className: 'border-slate-400/30 bg-slate-400/10 text-slate-300' },
};

function RatingSummary({ game }: { game: FamilyGameCard }) {
  const rating = game.rating ?? { count: 0, average: 0 };
  return (
    <span className="inline-flex items-center gap-1" data-testid={`family-rating-${game.slug}`}>
      <Star size={12} className={rating.count ? 'text-amber-300' : 'text-slate-500'} fill={rating.count ? 'currentColor' : 'none'} />
      {rating.count ? `${rating.average.toFixed(1)} · ${rating.count} ${rating.count === 1 ? 'vote' : 'votes'}` : 'No votes yet'}
    </span>
  );
}

/** Tap-sized 1-5 star vote. The gateway records it under the signed-in member. */
function VoteStars({ game }: { game: FamilyGameCard }) {
  const queryClient = useQueryClient();
  const { trigger } = useHaptics();
  const [chosen, setChosen] = useState(0);
  const rate = useMutation({
    mutationFn: (stars: number) => api.rateArcadeGame(game.slug, stars),
    onSuccess: (_data, stars) => {
      setChosen(stars);
      void trigger('success');
      void queryClient.invalidateQueries({ queryKey: FAMILY_KEY });
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-0.5 pt-1" role="group" aria-label={`Rate ${game.title}`} data-testid={`family-vote-${game.slug}`}>
      {[1, 2, 3, 4, 5].map((stars) => (
        <button
          key={stars}
          type="button"
          aria-label={`Rate ${game.title} ${stars} ${stars === 1 ? 'star' : 'stars'}`}
          aria-pressed={chosen === stars}
          disabled={rate.isPending}
          onClick={() => rate.mutate(stars)}
          className="flex h-11 w-10 items-center justify-center rounded-xl transition hover:bg-white/5 active:scale-90 disabled:opacity-50"
        >
          <Star
            size={20}
            className={stars <= chosen ? 'text-amber-300' : 'text-slate-500'}
            fill={stars <= chosen ? 'currentColor' : 'none'}
          />
        </button>
      ))}
      <span className="ml-1 text-[11px]" aria-live="polite">
        {rate.isSuccess && <span className="font-medium text-emerald-300">Thanks!</span>}
        {rate.isError && <span className="text-rose-300">Could not save</span>}
      </span>
    </div>
  );
}

/** A big, friendly shelf card: the whole art panel is the Play button. */
function ShelfCard({ game, playBase, onPlay }: { game: FamilyGameCard; playBase?: string; onPlay: () => void }) {
  const top = game.top_score;
  const tvHref = game.party && playBase ? `${playBase.replace(/\/$/, '')}${game.tv_path || '/tv'}` : null;
  return (
    <article
      className="group glass-panel flex flex-col overflow-hidden rounded-3xl border border-white/10 transition-all hover:border-white/20 hover:shadow-xl"
      data-testid={`family-game-${game.slug}`}
    >
      <button
        type="button"
        onClick={onPlay}
        aria-label={`Play ${game.title}`}
        className="relative flex h-40 w-full items-center justify-center overflow-hidden text-left transition active:scale-[0.99]"
      >
        <GameArtwork
          slug={game.slug}
          emoji={game.emoji || '🎮'}
          category={game.genre}
          art={game.art}
          className="h-full w-full"
        />
        <span className="absolute bottom-3 right-3 z-20 inline-flex h-11 items-center gap-1.5 rounded-full bg-white/95 px-4 text-sm font-bold text-slate-900 shadow-lg transition-transform group-hover:scale-105">
          <Play size={15} fill="currentColor" /> Play
        </span>
        {game.ages && (
          <span className="absolute left-3 top-3 z-20 rounded-full border border-white/20 bg-black/40 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur-md">
            Ages {game.ages}{game.players ? ` · ${game.players}P` : ''}
          </span>
        )}
        {game.genre && (
          <span className="absolute bottom-3 left-3 z-20 rounded-full border border-white/15 bg-black/35 px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-100 backdrop-blur-md">
            {game.genre}
          </span>
        )}
      </button>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-base font-bold leading-tight text-white">{game.title}</h3>
            {game.tagline && <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-400">{game.tagline}</p>}
          </div>
          {tvHref && (
            <a
              href={tvHref}
              target="_blank"
              rel="noopener noreferrer"
              data-testid={`family-tv-${game.slug}`}
              className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl border border-amber-400/40 bg-amber-400/15 px-3 text-xs font-semibold text-amber-200 transition hover:bg-amber-400/25"
            >
              <Tv size={13} /> TV Stage
            </a>
          )}
        </div>
        <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-white/5 pt-2.5 text-[11px] text-slate-300">
          <RatingSummary game={game} />
          {game.genre && <span className="capitalize text-slate-400">{game.genre}</span>}
          {top && typeof top.score === 'number' && (
            <span className="ml-auto inline-flex items-center gap-1 rounded-full border border-amber-400/25 bg-amber-400/10 px-2 py-0.5 font-mono text-[11px] font-semibold text-amber-200">
              <Trophy size={11} /> {top.initials} {top.score.toLocaleString()}
            </span>
          )}
        </div>
        <VoteStars game={game} />
      </div>
    </article>
  );
}

/** Admin row: test it, vote, then publish or unpublish. */
function AdminRow({
  game,
  onPlay,
  canPublish,
}: {
  game: FamilyGameCard;
  onPlay: () => void;
  canPublish: boolean;
}) {
  const queryClient = useQueryClient();
  const setState = useMutation({
    mutationFn: (state: FamilyGameState) => api.setFamilyGameState(game.slug, state),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: FAMILY_KEY }),
  });
  const badge = STATE_BADGE[game.family_state] ?? STATE_BADGE.testing;
  const playable = game.family_state !== 'unpublished';
  const action = (state: FamilyGameState, label: string, Icon: typeof Eye, tone: string) => (
    <button
      type="button"
      disabled={!canPublish || setState.isPending}
      onClick={() => setState.mutate(state)}
      className={`inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border px-3 text-xs font-semibold transition disabled:opacity-40 ${tone}`}
    >
      <Icon size={14} /> {label}
    </button>
  );
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-3.5 space-y-2" data-testid={`family-admin-${game.slug}`}>
      <div className="flex items-start gap-3">
        <GameArtwork
          slug={game.slug}
          emoji={game.emoji || '🎮'}
          category={game.genre}
          art={game.art}
          variant="thumb"
          className="h-12 w-12 text-2xl"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate font-semibold text-slate-100">{game.title}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider ${badge.className}`}>{badge.label}</span>
            {game.source === 'benchmark' && (
              <span
                className="max-w-full truncate rounded-full border border-purple-400/30 bg-purple-400/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-purple-200"
                title={game.model}
              >
                Benchmark{game.model ? ` · ${game.model}` : ''}
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
            <RatingSummary game={game} />
            {game.shelf_reason && <span className="text-amber-200/90" data-testid={`family-reason-${game.slug}`}>{game.shelf_reason}</span>}
          </div>
        </div>
      </div>
      {playable && <VoteStars game={game} />}
      <div className="mt-1 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap [&>*:last-child:nth-child(odd)]:col-span-2">
        {playable && (
          <button
            type="button"
            onClick={onPlay}
            className="inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/5 px-3 text-xs font-semibold text-slate-100"
          >
            <FlaskConical size={14} /> Test play
          </button>
        )}
        {game.family_state !== 'published' &&
          action('published', 'Publish', Eye, 'border-emerald-400/40 bg-emerald-500/15 text-emerald-100')}
        {game.family_state !== 'unpublished' &&
          action('unpublished', 'Unpublish', EyeOff, 'border-rose-400/30 bg-rose-500/10 text-rose-100')}
        {game.family_state === 'unpublished' &&
          action('testing', 'Back to testing', FlaskConical, 'border-sky-400/40 bg-sky-500/10 text-sky-100')}
      </div>
      {setState.isError && <p className="mt-1 text-[11px] text-rose-300">Could not change this game. Check the arcade admin token.</p>}
    </div>
  );
}

const PHASE_LABEL: Record<string, string> = {
  lobby: 'In the lobby',
  writing: 'Writing',
  voting: 'Voting',
  reveal: 'Reveal',
  finished: 'Finished',
};

/** Admin: live party-game rooms, so one left running can be ended for everyone. */
function PartyRoomsPanel() {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState('');
  const rooms = useQuery({
    queryKey: ['party-rooms'],
    queryFn: () => api.getPartyRooms(),
    refetchInterval: 10000,
  });
  const end = useMutation({
    mutationFn: (code: string) => api.endPartyRoom(code),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['party-rooms'] }),
  });
  const list: PartyRoomSummary[] = rooms.data?.rooms ?? [];
  if (rooms.isLoading || list.length === 0) return null;
  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" data-testid="family-party-rooms">
      <div className="flex min-h-11 items-center gap-2">
        <PartyPopper size={16} className="text-amber-300" />
        <span className="text-sm font-semibold text-slate-200">Live party rooms</span>
        <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold text-sky-200">Admin</span>
      </div>
      <ul className="mt-2 space-y-2">
        {list.map((room) => {
          const asking = confirming === room.code;
          return (
            <li
              key={room.code}
              className="flex items-center gap-3 rounded-xl border border-white/5 bg-white/5 p-3"
              data-testid={`party-room-${room.code}`}
            >
              <span className="rounded-lg bg-amber-400/90 px-2 py-1 font-mono text-sm font-bold tracking-widest text-slate-900">
                {room.code}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-slate-100">
                  {room.host_name}
                  {room.player_count > 1 ? ` + ${room.player_count - 1}` : ''}
                </p>
                <p className="truncate text-[11px] text-slate-400">
                  {PHASE_LABEL[room.phase] ?? room.phase}
                  {room.round ? ` · Round ${room.round}/${room.total_rounds}` : ''}
                  {room.stage_connected ? ' · On a TV' : ''}
                </p>
              </div>
              <button
                type="button"
                disabled={end.isPending}
                onClick={() => {
                  if (asking) {
                    setConfirming('');
                    end.mutate(room.code);
                  } else {
                    setConfirming(room.code);
                  }
                }}
                className={`inline-flex min-h-11 shrink-0 items-center gap-1 rounded-xl border px-3 text-xs font-semibold transition ${
                  asking ? 'border-rose-400/60 bg-rose-500/30 text-rose-50' : 'border-rose-400/30 bg-rose-500/10 text-rose-100'
                }`}
              >
                <Square size={12} /> {asking ? 'Tap to confirm' : 'End game'}
              </button>
            </li>
          );
        })}
      </ul>
      {end.isError && <p className="mt-1 text-[11px] text-rose-300">Could not end that room. Check the arcade admin token.</p>}
    </section>
  );
}

function AdminPanel({ data, onPlay }: { data: FamilyGamesResponse; onPlay: (game: FamilyGameCard) => void }) {
  const [open, setOpen] = useState(true);
  const total = data.review.length + data.unpublished.length + data.games.length;
  if (total === 0) return null;
  const canPublish = data.admin_token_configured !== false;
  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" data-testid="family-admin">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-2 text-left"
      >
        <FlaskConical size={16} className="text-sky-300" />
        <span className="text-sm font-semibold text-slate-200">Test & publish</span>
        <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold text-sky-200">Admin</span>
        <ChevronDown size={16} className={`ml-auto text-slate-400 transition ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="mt-2 space-y-3">
          <p className="text-xs text-slate-400">
            Players see a game as soon as you publish it. Family votes then set the order on the shelf. Test play
            here first; games from the benchmark arcade are only shown once they are published here too.
          </p>
          {!canPublish && (
            <p className="flex items-start gap-2 rounded-xl border border-amber-400/30 bg-amber-400/10 p-2 text-xs text-amber-100" data-testid="family-token-missing">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" /> ARCADE_ADMIN_TOKEN is not set on the gateway, so
              games cannot be published yet.
            </p>
          )}
          {data.review.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-[11px] uppercase tracking-wider text-slate-500">Waiting for review</h3>
              {data.review.map((game) => (
                <AdminRow key={game.slug} game={game} onPlay={() => onPlay(game)} canPublish={canPublish} />
              ))}
            </div>
          )}
          {data.games.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-[11px] uppercase tracking-wider text-slate-500">On the family shelf</h3>
              {data.games.map((game) => (
                <AdminRow key={game.slug} game={game} onPlay={() => onPlay(game)} canPublish={canPublish} />
              ))}
            </div>
          )}
          {data.unpublished.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-[11px] uppercase tracking-wider text-slate-500">Unpublished</h3>
              {data.unpublished.map((game) => (
                <AdminRow key={game.slug} game={game} onPlay={() => onPlay(game)} canPublish={canPublish} />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * Family Games: phone-first games for the whole family. Players see only the
 * games an admin approved that the family also rates highly; admins test,
 * vote and publish from the same screen. Scores land on the family board
 * automatically under the signed-in member.
 */
export default function FamilyGames() {
  const [playing, setPlaying] = useState<FamilyGameCard | null>(null);
  // A room to drop straight into ("?play=family-jubilee&room=DOVE", from the
  // home-screen widget's "playing now").
  const [launchRoom, setLaunchRoom] = useState('');
  const [searchParams, setSearchParams] = useSearchParams();
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const submitted = useRef(new Set<string>());
  const queryClient = useQueryClient();
  const { trigger } = useHaptics();
  const { data, isLoading } = useQuery<FamilyGamesResponse>({
    queryKey: FAMILY_KEY,
    queryFn: () => api.getFamilyGames(),
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
  const { data: scoreboard } = useQuery({
    queryKey: ['arcade-scoreboard', 'family'],
    queryFn: () => api.getArcadeScoreboard('family'),
    staleTime: 60_000,
    retry: false,
  });
  const me = scoreboard?.players?.find((player) => player.you);

  const submitScore = useMutation({
    mutationFn: ({ slug, score, captureId }: { slug: string; score: number; captureId: string }) =>
      api.submitArcadeScore(slug, score, captureId),
  });

  // The game posts each finished run through the arcade page; save it under
  // the signed-in member and tell the game where it landed.
  useEffect(() => {
    if (!playing || !data?.play_base) return;
    let origin: string;
    try {
      origin = new URL(data.play_base).origin;
    } catch {
      return;
    }
    const reply = (message: Record<string, unknown>) =>
      frameRef.current?.contentWindow?.postMessage({ source: 'arcade-host', slug: playing.slug, ...message }, origin);
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== origin) return;
      const message = event.data as {
        source?: string;
        type?: string;
        slug?: string;
        score?: unknown;
        capture_id?: unknown;
        payload?: Record<string, unknown> | null;
      } | null;
      if (message?.source === 'arcade-relay' && message.slug === playing.slug) {
        if (message.type === 'list-tvs') {
          void api
            .getArcadeTvs(Boolean(message.payload?.refresh))
            .then((res) => reply({ type: 'tvs', tvs: res?.tvs ?? [], available: res?.success !== false }))
            .catch(() => reply({ type: 'tvs', tvs: [], available: false }));
        } else if (message.type === 'cast') {
          const payload = message.payload ?? {};
          void api
            .castArcadeTv({
              screen_id: String(payload.screen_id ?? ''),
              room_code: String(payload.room_code ?? ''),
              // Which game's TV page: the gateway maps it (Jubilee's board, not the party stage).
              game: playing.slug,
              method: payload.method ? String(payload.method) : undefined,
              stop: Boolean(payload.stop),
            })
            .then((res) => reply({ type: 'cast-status', status: res?.success ? 'ok' : 'error', result: res }))
            .catch(() =>
              reply({ type: 'cast-status', status: 'error', result: { success: false, error: 'Jarvis could not reach the TV service.' } }),
            );
        }
        return;
      }
      if (message?.source !== 'arcade-score' || message.slug !== playing.slug) return;
      if (typeof message.score !== 'number' || !Number.isFinite(message.score)) return;
      if (typeof message.capture_id !== 'string' || !message.capture_id) return;
      if (submitted.current.has(message.capture_id)) return;
      submitted.current.add(message.capture_id);
      submitScore.mutate(
        { slug: playing.slug, score: Math.max(0, Math.trunc(message.score)), captureId: message.capture_id },
        {
          onSuccess: (result) => {
            reply({ type: 'saved', rank: result?.rank ?? null });
            void trigger('success');
            void queryClient.invalidateQueries({ queryKey: ['arcade-scoreboard'] });
            void queryClient.invalidateQueries({ queryKey: ['arcade-highscores'] });
            void queryClient.invalidateQueries({ queryKey: FAMILY_KEY });
          },
          onError: () => reply({ type: 'error' }),
        },
      );
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [playing, data?.play_base, submitScore, queryClient, trigger]);

  // Warm up before Play is tapped: open the connection to the game host and
  // fetch the shelf's games into the cache, so a launch is mostly local.
  const playBase = data?.play_base;
  const warmSlugs = [...(data?.games ?? []), ...(data?.review ?? [])].slice(0, 8).map((g) => g.slug).join(',');
  useEffect(() => {
    if (!playBase) return;
    let origin: string;
    try {
      origin = new URL(playBase).origin;
    } catch {
      return;
    }
    const links: HTMLLinkElement[] = [];
    const add = (rel: string, href: string) => {
      const link = document.createElement('link');
      link.rel = rel;
      link.href = href;
      if (rel === 'preconnect') link.crossOrigin = '';
      document.head.appendChild(link);
      links.push(link);
    };
    add('preconnect', origin);
    add('dns-prefetch', origin);
    for (const slug of warmSlugs ? warmSlugs.split(',') : []) add('prefetch', `${origin}/game/${slug}/index.html`);
    return () => links.forEach((link) => link.remove());
  }, [playBase, warmSlugs]);

  const open = (game: FamilyGameCard, room = '') => {
    submitted.current.clear();
    submitScore.reset();
    setLaunchRoom(room);
    setPlaying(game);
  };

  // "?play=<slug>[&room=<code>]" opens that game once the shelf has loaded
  // (a widget tap or a shared link), then leaves the URL clean so Back and a
  // reload do not reopen it.
  const wanted = searchParams.get('play') ?? '';
  const wantedRoom = (searchParams.get('room') ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
  useEffect(() => {
    if (!wanted || !data) return;
    const shelf = [...(data.games ?? []), ...(data.review ?? []), ...(data.unpublished ?? [])];
    const game = shelf.find((g) => g.slug === wanted);
    if (game) open(game, wantedRoom);
    const next = new URLSearchParams(searchParams);
    next.delete('play');
    next.delete('room');
    setSearchParams(next, { replace: true });
    // open is stable enough for a one-shot launch; re-running on its identity would reopen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, wantedRoom, data]);

  if (isLoading) {
    return (
      <div className="glass-panel animate-pulse rounded-2xl border border-white/5 p-4" data-testid="family-games-loading">
        <div className="h-4 w-36 rounded bg-white/10" />
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="h-48 rounded-3xl bg-white/5" />
          <div className="h-48 rounded-3xl bg-white/5" />
        </div>
      </div>
    );
  }

  if (!data || !data.arcade_available) {
    return (
      <div className="glass-panel rounded-2xl border border-white/5 p-4" data-testid="family-games-offline">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-slate-400" />
          <span className="font-semibold text-slate-200">Family Games</span>
        </div>
        <p className="mt-1 text-xs text-slate-400">
          The games are offline right now{data?.error ? ` (${data.error})` : ''}. They come back on their own; scores are safe.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="family-games">
      <div className="glass-panel relative overflow-hidden rounded-2xl border border-white/10 p-4 sm:p-5">
        <div className="pointer-events-none absolute -right-12 -top-12 h-32 w-32 rounded-full bg-pink-500/10 blur-3xl" aria-hidden />
        <div className="relative flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-pink-400/30 bg-pink-500/15 text-pink-300">
            <Sparkles size={18} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-bold text-white">Family Games</h2>
              <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-slate-300">
                {data.games.length} {data.games.length === 1 ? 'game' : 'games'}
              </span>
            </div>
            <p className="mt-0.5 text-xs leading-relaxed text-slate-400">
              Made for phones, tablets, and desktops. Only the games the family rates best make the shelf. Rate a game after you play it.
            </p>
          </div>
        </div>
      </div>

      {data.games.length === 0 ? (
        <div className="glass-panel rounded-2xl border border-white/10 p-5 text-center" data-testid="family-games-empty">
          <p className="text-xs text-slate-400">
            {data.is_admin
              ? 'Nothing on the shelf yet. Test the games below, vote, and publish the ones the family will love.'
              : 'New games are being tested. Check back soon!'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-3" data-testid="family-shelf">
          {data.games.map((game) => (
            <ShelfCard key={game.slug} game={game} playBase={data.play_base} onPlay={() => open(game)} />
          ))}
        </div>
      )}

      <FamilyLeaderboard games={data.games} onPlay={open} />

      {data.is_admin && <PartyRoomsPanel />}

      {data.is_admin && <AdminPanel data={data} onPlay={open} />}

      {playing && (
        <GamePlayer
          slug={playing.slug}
          title={playing.title}
          emoji={playing.emoji}
          art={playing.art}
          src={`${data.play_base}/play/${playing.slug}?embed=1&player=${encodeURIComponent(me?.initials ?? 'YOU')}&name=${encodeURIComponent(me?.display_name ?? '')}${launchRoom ? `&room=${launchRoom}#room=${launchRoom}` : ''}`}
          frameRef={frameRef}
          onClose={() => {
            setPlaying(null);
            setLaunchRoom('');
          }}
        />
      )}
    </div>
  );
}
