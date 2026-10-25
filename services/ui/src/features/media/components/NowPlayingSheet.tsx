import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Link } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import {
  ChevronDown, ChevronUp, Cast, Heart, ListMusic, Music, Pause, Play, Repeat, Repeat1, RotateCcw, RotateCw,
  Shuffle, SkipBack, SkipForward, Timer,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useOptionalWebPlayer } from '../../../context/webPlayerContext';
import { accentStyle, useArtworkAccent } from '../engine/artworkColor';
import { useHaptics } from '../../../hooks/useHaptics';
import { api } from '../../../services/api';
import { formatDuration } from '../format';
import { proxiedImage } from '../../../lib/mediaImage';
import { useMediaToken } from '../../../lib/mediaToken';
import OutputSheet from './OutputSheet';
import { Scrubber, VolumeSlider } from './Scrubber';
import { webPlayerAdapter } from '../engine/adapters/adapters';
import { nextRepeat, parseQueueModes } from '../engine/adapters/PlayerAdapter';
import type { RepeatMode } from '../types';

const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2] as const;
const SLEEP_OPTIONS = [15, 30, 45, 60] as const;

const iconButton =
  'flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-300 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-40';

/** `Date.now` behind a function: the React compiler flags the call itself as
 *  impure when it appears in a component body, even inside an event handler. */
function nowMs(): number {
  return Date.now();
}

function isBook(uri: string | null): boolean {
  return Boolean(uri && /audiobook|podcast/i.test(uri));
}

/**
 * The full-screen "Now Playing" view for the app's own web player.
 *
 * It is global on purpose: whatever page the person is on, the mini-player
 * opens this, and closing it returns them exactly where they were. Everything
 * here reads from the one shared player above the router, so the sheet never
 * owns playback state or fights the Media page over it.
 */
export default function NowPlayingSheet({
  open,
  onClose,
  onOpenQueue,
}: {
  open: boolean;
  onClose: () => void;
  onOpenQueue?: () => void;
}) {
  const player = useOptionalWebPlayer();
  const reduceMotion = useReducedMotion();
  const { trigger } = useHaptics();
  // MA hands out relative paths and provider URLs; only the gateway proxy can
  // fetch both with credentials, and the accent canvas needs the same origin.
  // The URL is signed with the media token, so re-render when it arrives.
  useMediaToken();
  const art = proxiedImage(player?.mediaImage, 512);
  // The cover's colour, readable on the dark sheet (§6.5); the theme's otherwise.
  const accentVars = accentStyle(useArtworkAccent(player?.mediaImage));

  const [favorite, setFavorite] = useState<{ uri: string; value: boolean } | null>(null);
  const [shuffle, setShuffle] = useState(false);
  const [repeatMode, setRepeatMode] = useState<RepeatMode>('off');
  const [sleepAt, setSleepAt] = useState<number | null>(null);
  const [sleepLabel, setSleepLabel] = useState('Sleep timer');
  const [sleepMenu, setSleepMenu] = useState(false);
  const [outputOpen, setOutputOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const close = useCallback(() => {
    setOutputOpen(false);
    onClose();
  }, [onClose]);

  const trackKey = player?.mediaUri || player?.mediaTitle || '';
  const duration = player?.duration ?? 0;
  const position = player?.position ?? 0;

  // A new track clears a favourite from the old one (the scrubber drops its own drag).
  const [lastTrack, setLastTrack] = useState(trackKey);
  if (lastTrack !== trackKey) {
    setLastTrack(trackKey);
    setFavorite(null);
  }

  const playerId = player?.playerId;
  const maCommand = player?.maCommand;
  useEffect(() => {
    if (!open || !playerId || !maCommand) return;
    let cancelled = false;
    void maCommand('player_queues/get', { queue_id: playerId })
      .then((queue) => {
        if (cancelled) return;
        const modes = parseQueueModes(queue);
        if (modes.shuffle !== null) setShuffle(modes.shuffle);
        if (modes.repeat !== null) setRepeatMode(modes.repeat);
      })
      .catch(() => {
        /* Not connected yet: the controls still work once the player is. */
      });
    return () => {
      cancelled = true;
    };
  }, [open, playerId, maCommand]);

  // Escape closes; the page behind must not scroll under a full-screen sheet.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, close]);

  // Android's back gesture and the browser's back button should close the
  // sheet, not quietly leave the page underneath. Push one same-URL entry on
  // open and consume it when closing so back never grows a history pile.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const previous = window.history.state;
    window.history.pushState({ ...(previous ?? {}), jarvisSheet: 'now-playing' }, '');
    let listening = true;
    const onPop = () => {
      // Landing on another sheet entry is not "back out of the sheet": it is
      // the stale back() of an earlier open (React's dev double-run of this
      // effect), which used to close the sheet a moment after it opened.
      if (!listening || window.history.state?.jarvisSheet === 'now-playing') return;
      listening = false;
      onCloseRef.current();
    };
    window.addEventListener('popstate', onPop);
    return () => {
      listening = false;
      window.removeEventListener('popstate', onPop);
      if (window.history.state?.jarvisSheet === 'now-playing') window.history.back();
    };
  }, [open]);

  const pauseRef = useRef(player?.pause);
  useEffect(() => {
    pauseRef.current = player?.pause;
  }, [player?.pause]);

  // The timer survives closing the sheet: it belongs to playback, not the view.
  useEffect(() => {
    if (sleepAt === null) return;
    const remaining = sleepAt - Date.now();
    const id = window.setTimeout(() => {
      pauseRef.current?.();
      setSleepAt(null);
      setSleepLabel('Sleep timer');
    }, Math.min(Math.max(0, remaining), 2_147_483_647));
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      window.clearTimeout(id);
      window.clearInterval(tick);
    };
  }, [sleepAt]);

  if (!player || !player.mediaTitle) return null;

  const book = isBook(player.mediaUri);
  const favoriteUri = player.mediaUri;
  const isFavorite = favorite && favorite.uri === favoriteUri ? favorite.value : Boolean(player.mediaFavorite);
  const native = Capacitor.isNativePlatform();
  const sleepLeft = sleepAt === null ? 0 : Math.max(0, Math.ceil((sleepAt - now) / 1000));

  const toggleFavorite = async () => {
    if (!favoriteUri) return;
    void trigger('light');
    const target = !isFavorite;
    setFavorite({ uri: favoriteUri, value: target });
    try {
      await api.setMediaFavorite(favoriteUri, target);
    } catch {
      setFavorite({ uri: favoriteUri, value: !target });
      toast.error('Could not update favorites');
    }
  };

  const toggleShuffle = async () => {
    if (!playerId) return;
    void trigger('light');
    const target = !shuffle;
    setShuffle(target);
    try {
      await webPlayerAdapter(player).setShuffle('', target);
    } catch {
      setShuffle(!target);
      toast.error('Could not change shuffle');
    }
  };

  const cycleRepeat = async () => {
    if (!playerId) return;
    void trigger('light');
    const target = nextRepeat(repeatMode);
    setRepeatMode(target);
    try {
      await webPlayerAdapter(player).setRepeat('', target);
    } catch {
      setRepeatMode(repeatMode);
      toast.error('Could not change repeat');
    }
  };

  const cycleSpeed = () => {
    void trigger('light');
    const index = SPEEDS.indexOf(player.playbackRate as (typeof SPEEDS)[number]);
    player.setPlaybackRate(SPEEDS[(index + 1) % SPEEDS.length]);
  };

  const startSleep = (minutes: number | 'track') => {
    void trigger('light');
    setSleepMenu(false);
    if (minutes === 'track') {
      const left = Math.max(0, duration - position);
      setSleepAt(nowMs() + left * 1000);
      setSleepLabel('End of track');
      return;
    }
    setSleepAt(nowMs() + minutes * 60_000);
    setSleepLabel(`${minutes} min`);
  };

  const cancelSleep = () => {
    setSleepAt(null);
    setSleepLabel('Sleep timer');
    setSleepMenu(false);
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-label="Now playing"
          data-testid="now-playing-sheet"
          data-reduced-motion={reduceMotion ? 'true' : 'false'}
          style={accentVars}
          className="fixed inset-0 z-[60] overflow-hidden"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.2 }}
        >
          <div className="absolute inset-0 bg-slate-950" />
          {art ? (
            <div
              aria-hidden
              className="absolute inset-0 scale-125 bg-cover bg-center opacity-40 blur-3xl"
              style={{ backgroundImage: `url("${art}")` }}
            />
          ) : (
            <div
              aria-hidden
              className="absolute inset-0 opacity-25"
              style={{ background: 'radial-gradient(60% 50% at 50% 20%, var(--media-accent), transparent 70%)' }}
            />
          )}
          <div aria-hidden className="absolute inset-0 bg-gradient-to-b from-slate-950/50 via-slate-950/75 to-slate-950" />

          <motion.div
            className="relative mx-auto flex h-[100dvh] w-full max-w-3xl flex-col px-5 pt-[calc(env(safe-area-inset-top,0px)+0.5rem)] pb-[calc(env(safe-area-inset-bottom,0px)+1.25rem)]"
            drag="y"
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0.02, bottom: 0.4 }}
            onDragEnd={(_, info) => {
              if (info.offset.y > 120 || info.velocity.y > 600) close();
            }}
            initial={{ y: reduceMotion ? 0 : 40 }}
            animate={{ y: 0 }}
            exit={{ y: reduceMotion ? 0 : 60 }}
            transition={reduceMotion ? { duration: 0 } : { type: 'spring', stiffness: 400, damping: 40 }}
          >
            <header className="flex items-center justify-between py-1">
              <button type="button" onClick={close} aria-label="Close now playing" className={iconButton}>
                <ChevronDown size={24} />
              </button>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">Playing here</p>
              <Link to="/media" onClick={close} aria-label="Open the Media page" className={iconButton}>
                <Music size={19} />
              </Link>
            </header>

            <div className="flex min-h-0 flex-1 flex-col justify-start gap-4 overflow-y-auto py-1 sm:justify-center sm:gap-6">
              <div className="flex justify-center">
                {art ? (
                  <motion.img
                    layoutId="np-art"
                    src={art}
                    alt=""
                    className="aspect-square w-[min(74vw,36dvh)] max-w-[380px] rounded-2xl object-cover shadow-2xl shadow-black/60"
                  />
                ) : (
                  <motion.div
                    layoutId="np-art"
                    className="flex aspect-square w-[min(74vw,36dvh)] max-w-[380px] items-center justify-center rounded-2xl shadow-2xl shadow-black/60"
                    style={{ background: 'linear-gradient(145deg, color-mix(in srgb, var(--media-accent) 33%, transparent), rgba(15,23,42,0.9))' }}
                  >
                    <Music size={64} className="text-white/60" aria-hidden />
                  </motion.div>
                )}
              </div>

              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="truncate text-xl font-bold text-white sm:text-3xl">{player.mediaTitle}</h2>
                  {player.mediaArtist ? <p className="mt-1 truncate text-sm text-slate-300 sm:text-base">{player.mediaArtist}</p> : null}
                </div>
                {favoriteUri ? (
                  <button
                    type="button"
                    onClick={() => void toggleFavorite()}
                    aria-label={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
                    aria-pressed={isFavorite}
                    className={iconButton}
                  >
                    <Heart size={22} className={isFavorite ? 'fill-current text-[var(--media-accent,var(--site-accent-text))]' : ''} />
                  </button>
                ) : null}
              </div>

              <Scrubber
                position={position}
                duration={duration}
                trackKey={trackKey}
                onSeek={(seconds) => void player.seek(seconds)}
              />

              <div className="flex items-center justify-center gap-2 sm:gap-4">
                {book ? (
                  <button
                    type="button"
                    onClick={() => void player.seek(Math.max(0, position - 15))}
                    aria-label="Back 15 seconds"
                    className={iconButton}
                  >
                    <RotateCcw size={23} />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void toggleShuffle()}
                    aria-label="Shuffle"
                    aria-pressed={shuffle}
                    className={`${iconButton} ${shuffle ? 'text-[var(--media-accent,var(--site-accent-text))]' : ''}`}
                  >
                    <Shuffle size={21} />
                  </button>
                )}

                <button type="button" onClick={() => void player.previous()} aria-label="Previous track" className={iconButton}>
                  <SkipBack size={26} />
                </button>

                <button
                  type="button"
                  onClick={() => {
                    void trigger('light');
                    void (player.isPlaying ? player.pause() : player.play());
                  }}
                  aria-label={player.isPlaying ? 'Pause' : 'Play'}
                  className="flex h-[72px] w-[72px] items-center justify-center rounded-full bg-white text-slate-900 shadow-xl shadow-black/40 transition-transform hover:scale-105 active:scale-95"
                >
                  {player.isPlaying ? <Pause size={30} className="fill-current" /> : <Play size={30} className="ml-1 fill-current" />}
                </button>

                <button type="button" onClick={() => void player.next()} aria-label="Next track" className={iconButton}>
                  <SkipForward size={26} />
                </button>

                {book ? (
                  <button
                    type="button"
                    onClick={() => void player.seek(Math.min(duration, position + 30))}
                    aria-label="Forward 30 seconds"
                    className={iconButton}
                  >
                    <RotateCw size={23} />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void cycleRepeat()}
                    aria-label={`Repeat ${repeatMode}`}
                    aria-pressed={repeatMode !== 'off'}
                    className={`${iconButton} ${repeatMode !== 'off' ? 'text-[var(--media-accent,var(--site-accent-text))]' : ''}`}
                  >
                    {repeatMode === 'one' ? <Repeat1 size={21} /> : <Repeat size={21} />}
                  </button>
                )}
              </div>

              {book ? (
                <div className="flex justify-center">
                  <button
                    type="button"
                    onClick={cycleSpeed}
                    aria-label="Playback speed"
                    className="rounded-xl border border-white/15 px-4 py-2 text-sm font-semibold text-white hover:bg-white/10"
                  >
                    {player.playbackRate}×
                  </button>
                </div>
              ) : null}

              {!native ? (
                <VolumeSlider
                  volume={player.volume}
                  muted={player.muted}
                  onChange={player.setVolume}
                  onToggleMute={() => player.setMuted(!player.muted)}
                />
              ) : null}
            </div>

            <div className="space-y-2">
              {sleepMenu ? (
                <div className="flex flex-wrap justify-center gap-2" role="group" aria-label="Sleep timer length">
                  {SLEEP_OPTIONS.map((minutes) => (
                    <button
                      key={minutes}
                      type="button"
                      onClick={() => startSleep(minutes)}
                      className="min-h-11 rounded-xl border border-white/15 px-3 text-sm text-white hover:bg-white/10"
                    >
                      {minutes} min
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => startSleep('track')}
                    disabled={duration <= position}
                    className="min-h-11 rounded-xl border border-white/15 px-3 text-sm text-white hover:bg-white/10 disabled:opacity-40"
                  >
                    End of track
                  </button>
                  {sleepAt !== null ? (
                    <button
                      type="button"
                      onClick={cancelSleep}
                      className="min-h-11 rounded-xl border border-rose-400/30 px-3 text-sm text-rose-200 hover:bg-rose-400/10"
                    >
                      Cancel
                    </button>
                  ) : null}
                </div>
              ) : null}

              <div className="flex items-center justify-between gap-2 border-t border-white/10 pt-2">
                <button
                  type="button"
                  onClick={() => setOutputOpen(true)}
                  aria-label="Choose output"
                  aria-haspopup="dialog"
                  className="flex min-h-11 min-w-0 items-center gap-1.5 rounded-xl px-2 text-xs text-slate-400 hover:bg-white/10 hover:text-white"
                >
                  <Cast size={15} aria-hidden />
                  <span className="hidden truncate sm:inline">This device</span>
                  <ChevronUp size={13} aria-hidden />
                </button>
                <button
                  type="button"
                  onClick={() => onOpenQueue?.()}
                  aria-label="Open queue"
                  aria-haspopup="dialog"
                  className="flex min-h-11 items-center gap-1.5 rounded-xl px-2 text-xs text-slate-400 hover:bg-white/10 hover:text-white"
                >
                  <ListMusic size={15} aria-hidden />
                  <span>Queue</span>
                  <ChevronUp size={13} aria-hidden />
                </button>
                <button
                  type="button"
                  onClick={() => setSleepMenu((value) => !value)}
                  aria-expanded={sleepMenu}
                  aria-label="Sleep timer"
                  className={`flex min-h-11 items-center gap-1.5 rounded-xl px-3 text-xs ${sleepAt !== null ? 'bg-[var(--site-accent-soft)] text-[var(--media-accent,var(--site-accent-text))]' : 'text-slate-300 hover:bg-white/10'}`}
                >
                  <Timer size={16} aria-hidden />
                  <span className="max-w-[38vw] truncate">
                    {sleepAt !== null ? `${sleepLabel} · ${formatDuration(sleepLeft)}` : 'Sleep timer'}
                  </span>
                </button>
              </div>
            </div>
          </motion.div>

          <OutputSheet open={open && outputOpen} onClose={() => setOutputOpen(false)} onTransferred={close} />
        </motion.div>
      )}
    </AnimatePresence>
  );
}
