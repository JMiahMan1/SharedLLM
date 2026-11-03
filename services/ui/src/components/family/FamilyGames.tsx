import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ChevronDown, Eye, EyeOff, FlaskConical, Play, Sparkles, Star, Trophy, Users } from 'lucide-react';
import Modal from '../ui/Modal';
import ArcadeHighscores from './ArcadeHighscores';
import ArcadeScoreboard from './ArcadeScoreboard';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { FamilyGameCard, FamilyGameState, FamilyGamesResponse } from '../../types/api';

const FAMILY_KEY = ['family-games'];

const STATE_BADGE: Record<FamilyGameState, { label: string; className: string }> = {
  testing: { label: 'Testing', className: 'border-sky-400/40 bg-sky-400/10 text-sky-200' },
  published: { label: 'Published', className: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200' },
  unpublished: { label: 'Unpublished', className: 'border-slate-400/30 bg-slate-400/10 text-slate-300' },
};

function artStyle(game: FamilyGameCard): React.CSSProperties {
  const from = game.art?.from || '#7c5cff';
  const to = game.art?.to || '#ff7ab6';
  return { backgroundImage: `linear-gradient(135deg, ${from}, ${to})` };
}

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
    <div className="flex items-center gap-0.5" role="group" aria-label={`Rate ${game.title}`} data-testid={`family-vote-${game.slug}`}>
      {[1, 2, 3, 4, 5].map((stars) => (
        <button
          key={stars}
          type="button"
          aria-label={`Rate ${game.title} ${stars} ${stars === 1 ? 'star' : 'stars'}`}
          aria-pressed={chosen === stars}
          disabled={rate.isPending}
          onClick={() => rate.mutate(stars)}
          className="flex h-11 w-9 items-center justify-center rounded-lg transition active:scale-90 disabled:opacity-50"
        >
          <Star
            size={20}
            className={stars <= chosen ? 'text-amber-300' : 'text-slate-500'}
            fill={stars <= chosen ? 'currentColor' : 'none'}
          />
        </button>
      ))}
      <span className="ml-1 text-[11px]" aria-live="polite">
        {rate.isSuccess && <span className="text-emerald-300">Thanks!</span>}
        {rate.isError && <span className="text-rose-300">Could not save</span>}
      </span>
    </div>
  );
}

/** A big, friendly shelf card: the whole art panel is the Play button. */
function ShelfCard({ game, onPlay }: { game: FamilyGameCard; onPlay: () => void }) {
  const top = game.top_score;
  return (
    <article
      className="glass-panel flex flex-col overflow-hidden rounded-3xl border border-white/5"
      data-testid={`family-game-${game.slug}`}
    >
      <button
        type="button"
        onClick={onPlay}
        aria-label={`Play ${game.title}`}
        className="relative flex h-36 items-center justify-center text-6xl transition active:scale-[0.98]"
        style={artStyle(game)}
      >
        <span className="drop-shadow-lg" aria-hidden>{game.emoji || '🎮'}</span>
        <span className="absolute bottom-3 right-3 inline-flex h-11 items-center gap-1.5 rounded-full bg-white/90 px-4 text-sm font-bold text-slate-900 shadow-lg">
          <Play size={15} fill="currentColor" /> Play
        </span>
        {game.ages && (
          <span className="absolute left-3 top-3 rounded-full bg-black/25 px-2 py-0.5 text-[11px] font-semibold text-white backdrop-blur">
            Ages {game.ages}
          </span>
        )}
      </button>
      <div className="flex flex-1 flex-col gap-1.5 p-4">
        <h3 className="text-base font-bold leading-tight text-slate-100">{game.title}</h3>
        {game.tagline && <p className="text-xs text-slate-400">{game.tagline}</p>}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
          <RatingSummary game={game} />
          {game.genre && <span className="capitalize">{game.genre}</span>}
          {top && typeof top.score === 'number' && (
            <span className="inline-flex items-center gap-1">
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
    <div className="rounded-2xl border border-white/10 bg-white/5 p-3" data-testid={`family-admin-${game.slug}`}>
      <div className="flex items-start gap-3">
        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl text-2xl" style={artStyle(game)} aria-hidden>
          {game.emoji || '🎮'}
        </span>
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
            Players see a game only after you publish it <b>and</b> the family rates it at least {data.min_rating}★ from{' '}
            {data.min_votes} or more votes. Test play and vote here first.
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
  const me = scoreboard?.players.find((player) => player.you);

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
      const message = event.data as { source?: string; slug?: string; score?: unknown; capture_id?: unknown } | null;
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

  const open = (game: FamilyGameCard) => {
    submitted.current.clear();
    submitScore.reset();
    setPlaying(game);
  };

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
      <div className="glass-panel rounded-2xl border border-white/5 p-4">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-pink-300" />
          <h2 className="text-sm font-semibold text-slate-200">Family Games</h2>
          <span className="ml-auto text-[10px] uppercase tracking-wider text-slate-500">
            {data.games.length} {data.games.length === 1 ? 'game' : 'games'}
          </span>
        </div>
        <p className="mt-1 text-xs text-slate-400">
          Made for phones. Only the games the family rates best make the shelf. Rate a game after you play it.
        </p>
      </div>

      {data.games.length === 0 ? (
        <div className="glass-panel rounded-2xl border border-white/5 p-4" data-testid="family-games-empty">
          <p className="text-xs text-slate-400">
            {data.is_admin
              ? 'Nothing on the shelf yet. Test the games below, vote, and publish the ones the family will love.'
              : 'New games are being tested. Check back soon!'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="family-shelf">
          {data.games.map((game) => (
            <ShelfCard key={game.slug} game={game} onPlay={() => open(game)} />
          ))}
        </div>
      )}

      <ArcadeScoreboard
        collection="family"
        title="Family Games scoreboard"
        description="Every finished game counts for the player who is signed in. No typing, no cheating."
      />
      <ArcadeHighscores collection="family" />

      {data.is_admin && <AdminPanel data={data} onPlay={open} />}

      <Modal isOpen={playing !== null} onClose={() => setPlaying(null)} title={playing?.title} fullScreen>
        {playing && (
          <div className="flex min-h-0 flex-1 flex-col">
            <iframe
              ref={frameRef}
              src={`${data.play_base}/play/${playing.slug}?embed=1&player=${encodeURIComponent(me?.initials ?? 'YOU')}`}
              title={`${playing.title} game`}
              data-testid="family-player-frame"
              className="min-h-0 w-full flex-1 border-0 bg-black sm:rounded-xl"
              allow="autoplay; fullscreen; gamepad"
              allowFullScreen
            />
            <div className="flex flex-wrap items-center gap-2 px-1 pt-2 pb-[env(safe-area-inset-bottom)] text-[11px] text-slate-400">
              <Users size={12} />
              <span>
                Playing as <b className="text-slate-200">{me?.display_name ?? me?.initials ?? 'you'}</b>
              </span>
              {playing.family_state === 'testing' && (
                <span className="rounded-full border border-sky-400/40 bg-sky-400/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-sky-200">
                  Testing
                </span>
              )}
              <span className="ml-auto" aria-live="polite">
                {submitScore.isPending && <span className="text-sky-300">Saving score…</span>}
                {submitScore.isSuccess && <span className="text-emerald-300">Score saved</span>}
                {submitScore.isError && <span className="text-rose-300">Could not save the score</span>}
              </span>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
