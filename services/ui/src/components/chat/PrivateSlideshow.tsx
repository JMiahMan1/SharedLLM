import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { ChevronLeft, ChevronRight, Loader2, Maximize, Minimize, Pause, Play, Repeat, RotateCcw, Settings2, Shuffle, X } from 'lucide-react';
import type { PrivateVault } from '../../lib/privateChatCrypto';
import {
  kenBurns,
  seededRandom,
  SLIDE_INTERVALS,
  slideshowOrder,
  stepSlideshow,
  type GalleryItem,
  type SlideTransition,
} from './privateGalleryModel';
import { usePrivateVideo } from './usePrivateVideo';
import { noSave, usePrivatePicture } from './usePrivatePicture';

const HIDE_CONTROLS_MS = 2600;
const FADE_MS = 900;
const SWIPE_PX = 50;

/** The viewer's own slideshow settings, remembered on this device (never anything about the pictures). */
const SETTINGS_KEY = 'private-slideshow-settings';

interface Settings {
  interval: number;
  shuffle: boolean;
  loop: boolean;
  transition: SlideTransition;
}

const DEFAULTS: Settings = { interval: 5, shuffle: false, loop: true, transition: 'kenburns' };

function loadSettings(): Settings {
  try {
    const raw = JSON.parse(window.localStorage.getItem(SETTINGS_KEY) || '{}') as Partial<Settings>;
    return {
      interval: SLIDE_INTERVALS.includes(raw.interval as (typeof SLIDE_INTERVALS)[number]) ? (raw.interval as number) : DEFAULTS.interval,
      shuffle: typeof raw.shuffle === 'boolean' ? raw.shuffle : DEFAULTS.shuffle,
      loop: typeof raw.loop === 'boolean' ? raw.loop : DEFAULTS.loop,
      transition: raw.transition === 'fade' || raw.transition === 'kenburns' ? raw.transition : DEFAULTS.transition,
    };
  } catch {
    return DEFAULTS;
  }
}

function saveSettings(settings: Settings): void {
  try {
    window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // A private window: the settings just are not remembered.
  }
}

/** Keep the screen on while the slideshow plays, where the browser can (Wake Lock API). */
function useWakeLock(active: boolean): void {
  useEffect(() => {
    const wake = (navigator as Navigator & { wakeLock?: { request: (type: 'screen') => Promise<{ release: () => Promise<void> }> } }).wakeLock;
    if (!active || !wake) return undefined;
    let lock: { release: () => Promise<void> } | null = null;
    let live = true;
    const take = () => {
      if (document.visibilityState !== 'visible') return;
      void wake
        .request('screen')
        .then((made) => {
          if (live) lock = made;
          else void made.release().catch(() => {});
        })
        .catch(() => {});
    };
    take();
    // The browser drops the lock when the page is hidden; take it again on return.
    document.addEventListener('visibilitychange', take);
    return () => {
      live = false;
      document.removeEventListener('visibilitychange', take);
      void lock?.release().catch(() => {});
    };
  }, [active]);
}

/**
 * One slide: the picture (shared with its tile, decrypted in memory), or a clip
 * decrypted when its turn comes and played through to the end.
 */
function Slide({
  token,
  vault,
  item,
  current,
  transition,
  interval,
  playing,
  onReady,
  onVideoEnd,
  onVideoProgress,
}: {
  token: string;
  vault: PrivateVault;
  item: GalleryItem;
  current: boolean;
  transition: SlideTransition;
  interval: number;
  playing: boolean;
  onReady: () => void;
  onVideoEnd: () => void;
  onVideoProgress: (fraction: number) => void;
}) {
  const { url, failed } = usePrivatePicture(token, item.blob, item.mime, vault);
  const clip = usePrivateVideo(token, current && item.video ? item.video : null, vault);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [loaded, setLoaded] = useState(false);
  const ready = Boolean(item.video ? clip.url : loaded) || failed || Boolean(clip.failed);

  useEffect(() => {
    if (current && ready) onReady();
  }, [current, ready, onReady]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !current) return;
    if (playing) {
      video.play().catch(() => {
        // Sound before a tap can be refused: play muted rather than not at all.
        video.muted = true;
        void video.play().catch(() => {});
      });
    } else {
      video.pause();
    }
  }, [playing, current, clip.url]);

  const move = kenBurns(item.blob);
  // A leaving slide holds where its drift ended while the next one fades in over it.
  const kenStyle: CSSProperties | undefined =
    transition === 'kenburns' && !item.video && !current
      ? { transform: `translate3d(${move.toX}%, ${move.toY}%, 0) scale(${move.toScale})` }
      : transition === 'kenburns' && !item.video && loaded
      ? ({
          '--kb-fx': `${move.fromX}%`,
          '--kb-fy': `${move.fromY}%`,
          '--kb-tx': `${move.toX}%`,
          '--kb-ty': `${move.toY}%`,
          '--kb-fs': move.fromScale,
          '--kb-ts': move.toScale,
          animation: `pg-kenburns ${interval + FADE_MS / 1000}s ease-in-out forwards`,
          animationPlayState: playing ? 'running' : 'paused',
        } as CSSProperties)
      : undefined;

  return (
    <div
      className="absolute inset-0 flex items-center justify-center overflow-hidden"
      style={{ animation: `pg-slide-in ${FADE_MS}ms ease-in-out both` }}
      data-testid={current ? 'private-slideshow-current' : 'private-slideshow-leaving'}
      data-blob={item.blob}
      aria-hidden={!current}
    >
      {url && (
        // The same picture, blurred, fills the bars around a picture of another shape.
        <img src={url} alt="" aria-hidden {...noSave} className="pointer-events-none absolute inset-0 h-full w-full scale-110 select-none object-cover opacity-40 blur-2xl" />
      )}
      {item.video && clip.url ? (
        <video
          ref={videoRef}
          src={clip.url}
          poster={url || undefined}
          playsInline
          autoPlay={playing}
          controlsList="nodownload noremoteplayback"
          disablePictureInPicture
          disableRemotePlayback
          {...noSave}
          onEnded={onVideoEnd}
          onTimeUpdate={(event) => {
            const v = event.currentTarget;
            if (v.duration > 0) onVideoProgress(v.currentTime / v.duration);
          }}
          className="relative max-h-full max-w-full select-none object-contain"
          data-testid="private-slideshow-video"
        />
      ) : url ? (
        <img
          src={url}
          alt={item.caption || (item.video ? 'Video' : 'Photo')}
          {...noSave}
          onLoad={() => setLoaded(true)}
          style={kenStyle}
          // Fills the screen at its own shape, small pictures scaled up like any slideshow.
          className="pointer-events-none relative h-full w-full select-none object-contain [-webkit-touch-callout:none]"
          data-testid="private-slideshow-photo"
        />
      ) : failed ? (
        <p className="relative text-sm text-slate-400">This picture could not be opened.</p>
      ) : (
        <Loader2 size={28} className="relative animate-spin text-white/70" />
      )}
      {item.video && current && !clip.url && !clip.failed && url && (
        <span className="absolute bottom-24 rounded-full bg-black/60 px-3 py-1 text-xs text-white" role="status">
          {clip.progress.count > 1 ? `Decrypting ${clip.progress.done} of ${clip.progress.count}` : 'Decrypting video'}
        </span>
      )}
    </div>
  );
}

/**
 * The private gallery as a slideshow: full screen, the screen kept awake,
 * photos crossfading or drifting (Ken Burns) for a chosen time each, clips
 * played through, shuffled or not, looping or not. Swipe, arrow keys or the
 * buttons skip; Space pauses; Esc leaves. The controls fade away while it
 * plays and come back on any touch or movement. Everything shown is decrypted
 * in memory for this screen and let go when it closes.
 */
export default function PrivateSlideshow({
  token,
  vault,
  items,
  startIndex,
  onClose,
}: {
  token: string;
  vault: PrivateVault;
  /** What to show: the whole gallery as filtered, or a selection. */
  items: GalleryItem[];
  startIndex: number;
  /** Closed, on the picture last shown. */
  onClose: (last?: GalleryItem) => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const update = (patch: Partial<Settings>) =>
    setSettings((current) => {
      const next = { ...current, ...patch };
      saveSettings(next);
      return next;
    });
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 2 ** 31));
  const [playing, setPlaying] = useState(true);
  const [ended, setEnded] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [controls, setControls] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [readyBlob, setReadyBlob] = useState('');
  const [videoProgress, setVideoProgress] = useState(0);

  // The order, and where in it we are. Shuffling keeps the picture on screen and reorders the rest.
  const [anchor, setAnchor] = useState(startIndex);
  const order = useMemo(
    () => slideshowOrder(items.length, anchor, settings.shuffle, seededRandom(seed)),
    [items.length, anchor, settings.shuffle, seed],
  );
  const [position, setPosition] = useState(0);
  const [previous, setPrevious] = useState<GalleryItem | null>(null);
  const item = items[order[position] ?? 0];
  const upcoming = items[order[stepSlideshow(position, order.length, 1, settings.loop) ?? position] ?? 0];

  const goTo = useCallback(
    (delta: number, fromUser = false) => {
      const next = stepSlideshow(position, order.length, delta, settings.loop);
      if (next === null) {
        if (!fromUser) {
          setEnded(true);
          setPlaying(false);
        }
        return;
      }
      setPrevious(item ?? null);
      setPosition(next);
      setVideoProgress(0);
      setEnded(false);
    },
    [position, order.length, settings.loop, item],
  );

  // The leaving slide stays under the new one for the length of the crossfade.
  useEffect(() => {
    if (!previous) return undefined;
    const timer = window.setTimeout(() => setPrevious(null), FADE_MS + 50);
    return () => window.clearTimeout(timer);
  }, [previous]);

  const ready = Boolean(item) && readyBlob === item?.blob;
  const markReady = useCallback(() => setReadyBlob(item?.blob ?? ''), [item?.blob]);

  // A photo stays up for the chosen time once it is on screen; a clip advances when it ends.
  useEffect(() => {
    if (!playing || !ready || !item || item.video) return undefined;
    const timer = window.setTimeout(() => goTo(1), settings.interval * 1000);
    return () => window.clearTimeout(timer);
  }, [playing, ready, item, settings.interval, goTo]);

  useWakeLock(playing && !ended);

  // Controls fade out while it plays, and come back on any movement or touch.
  const hideTimer = useRef<number | undefined>(undefined);
  const poke = useCallback(() => {
    setControls(true);
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setControls(false), HIDE_CONTROLS_MS);
  }, []);
  useEffect(() => {
    window.clearTimeout(hideTimer.current);
    if (playing && !showSettings && !ended) hideTimer.current = window.setTimeout(() => setControls(false), HIDE_CONTROLS_MS);
    return () => window.clearTimeout(hideTimer.current);
  }, [playing, showSettings, ended]);
  const shown = controls || !playing || showSettings || ended;

  // Full screen where the browser allows it; the slideshow still fills the window where it does not.
  const toggleFullscreen = useCallback(() => {
    const doc = document as Document & { webkitFullscreenElement?: Element; webkitExitFullscreen?: () => Promise<void> };
    const el = rootRef.current as (HTMLDivElement & { webkitRequestFullscreen?: () => Promise<void> }) | null;
    if (document.fullscreenElement || doc.webkitFullscreenElement) {
      void (document.exitFullscreen?.() ?? doc.webkitExitFullscreen?.())?.catch?.(() => {});
    } else if (el) {
      void (el.requestFullscreen?.({ navigationUI: 'hide' }) ?? el.webkitRequestFullscreen?.())?.catch?.(() => {});
    }
  }, []);
  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onChange);
    // Opened by a tap, so the browser lets it go full screen straight away.
    toggleFullscreen();
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
      if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
    };
  }, [toggleFullscreen]);

  const close = useCallback(() => onClose(item), [onClose, item]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (showSettings) setShowSettings(false);
        else close();
      } else if (event.key === 'ArrowRight') {
        goTo(1, true);
        poke();
      } else if (event.key === 'ArrowLeft') {
        goTo(-1, true);
        poke();
      } else if (event.key === ' ' || event.key === 'k') {
        event.preventDefault();
        setPlaying((p) => !p);
        poke();
      } else if (event.key === 'f') {
        toggleFullscreen();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [close, goTo, poke, showSettings, toggleFullscreen]);

  // The chat locked under the slideshow: nothing decrypted may stay on screen.
  useEffect(() => vault.subscribe(() => !vault.unlocked && onClose()), [vault, onClose]);

  const swipe = useRef<{ x: number; y: number } | null>(null);

  if (!item) return null;
  const restart = () => {
    setSeed(Math.floor(Math.random() * 2 ** 31));
    setAnchor(order[0] ?? 0);
    setPosition(0);
    setEnded(false);
    setPlaying(true);
  };
  const progressKey = `${position}:${item.blob}:${settings.interval}:${ready}`;

  return (
    <div
      ref={rootRef}
      className={`fixed inset-0 z-[95] overflow-hidden bg-black text-white ${shown ? '' : 'cursor-none'}`}
      role="dialog"
      aria-modal="true"
      aria-label="Slideshow"
      data-testid="private-slideshow"
      data-playing={playing ? 'true' : 'false'}
      data-fullscreen={fullscreen ? 'true' : 'false'}
      onPointerMove={(event) => event.pointerType === 'mouse' && poke()}
      {...noSave}
    >
      <div
        className="absolute inset-0 touch-none"
        data-testid="private-slideshow-stage"
        onPointerDown={(event) => {
          swipe.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerUp={(event) => {
          const from = swipe.current;
          swipe.current = null;
          if (!from) return;
          const dx = event.clientX - from.x;
          const dy = event.clientY - from.y;
          if (Math.abs(dx) > SWIPE_PX && Math.abs(dx) > Math.abs(dy)) {
            goTo(dx < 0 ? 1 : -1, true);
            poke();
          } else if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
            // A tap shows the controls, or hides them when they are up.
            if (shown && playing) setControls(false);
            else poke();
          }
        }}
      >
        {previous && previous.blob !== item.blob && (
          <Slide
            key={`leaving-${previous.blob}`}
            token={token}
            vault={vault}
            item={previous}
            current={false}
            transition={settings.transition}
            interval={settings.interval}
            playing={false}
            onReady={() => {}}
            onVideoEnd={() => {}}
            onVideoProgress={() => {}}
          />
        )}
        <Slide
          key={`${position}-${item.blob}`}
          token={token}
          vault={vault}
          item={item}
          current
          transition={settings.transition}
          interval={settings.interval}
          playing={playing}
          onReady={markReady}
          onVideoEnd={() => playing && goTo(1)}
          onVideoProgress={setVideoProgress}
        />
        {upcoming && upcoming.blob !== item.blob && <Upcoming token={token} vault={vault} item={upcoming} />}
      </div>

      {/* Progress through this slide, and through the show. */}
      <div className={`pointer-events-none absolute inset-x-0 top-0 z-10 transition-opacity duration-500 ${shown ? 'opacity-100' : 'opacity-60'}`}>
        <div className="h-1 w-full bg-white/15" data-testid="private-slideshow-progress">
          {item.video ? (
            <div className="h-full origin-left bg-white" style={{ transform: `scaleX(${videoProgress})` }} />
          ) : (
            <div
              key={progressKey}
              className="h-full origin-left bg-white"
              style={{
                transform: 'scaleX(0)',
                animation: ready ? `pg-progress ${settings.interval}s linear forwards` : undefined,
                animationPlayState: playing ? 'running' : 'paused',
              }}
            />
          )}
        </div>
      </div>

      {/* Top bar */}
      <div
        className={`absolute inset-x-0 top-0 z-10 flex items-center gap-2 bg-gradient-to-b from-black/70 to-transparent px-2 pb-8 pt-[max(0.75rem,var(--safe-top))] transition-opacity duration-500 ${
          shown ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        data-testid="private-slideshow-top"
      >
        <button type="button" onClick={close} aria-label="Close slideshow" className="flex h-11 w-11 items-center justify-center rounded-full bg-white/10 backdrop-blur hover:bg-white/20">
          <X size={20} />
        </button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{item.own ? 'You' : item.actorName || 'Your partner'}</p>
          <p className="truncate text-xs tabular-nums text-white/70" data-testid="private-slideshow-position">
            {position + 1} of {order.length}
            {settings.shuffle ? ' · shuffled' : ''}
          </p>
        </div>
        <button
          type="button"
          onClick={toggleFullscreen}
          aria-label={fullscreen ? 'Leave full screen' : 'Full screen'}
          aria-pressed={fullscreen}
          className="flex h-11 w-11 items-center justify-center rounded-full bg-white/10 backdrop-blur hover:bg-white/20"
        >
          {fullscreen ? <Minimize size={18} /> : <Maximize size={18} />}
        </button>
      </div>

      {/* The end of a show that does not loop. */}
      {ended && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm" data-testid="private-slideshow-end">
          <div className="w-full max-w-xs rounded-3xl border border-white/10 bg-slate-900/90 p-5 text-center shadow-2xl">
            <p className="text-lg font-bold">That was all {order.length}</p>
            <p className="mt-1 text-sm text-white/70">Play them again, or go back to the gallery.</p>
            <div className="mt-4 flex gap-2">
              <button type="button" onClick={restart} className="flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-2xl bg-rose-600 font-semibold hover:bg-rose-500">
                <RotateCcw size={16} aria-hidden /> Again
              </button>
              <button type="button" onClick={close} className="flex min-h-11 flex-1 items-center justify-center rounded-2xl bg-white/10 font-semibold hover:bg-white/20">
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Settings */}
      {showSettings && (
        <div
          className="absolute inset-x-2 bottom-[calc(5.5rem+var(--safe-bottom))] z-20 mx-auto max-w-sm rounded-3xl border border-white/10 bg-slate-900/95 p-4 shadow-2xl backdrop-blur"
          role="group"
          aria-label="Slideshow settings"
          data-testid="private-slideshow-settings"
        >
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/60">Each photo for</p>
          <div className="mb-4 grid grid-cols-4 gap-1.5">
            {SLIDE_INTERVALS.map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={settings.interval === s}
                onClick={() => update({ interval: s })}
                className={`min-h-11 rounded-xl text-sm font-semibold transition ${settings.interval === s ? 'bg-rose-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/15'}`}
              >
                {s}s
              </button>
            ))}
          </div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/60">Transition</p>
          <div className="mb-4 grid grid-cols-2 gap-1.5">
            {(
              [
                ['kenburns', 'Pan and zoom'],
                ['fade', 'Crossfade'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={settings.transition === value}
                onClick={() => update({ transition: value })}
                className={`min-h-11 rounded-xl text-sm font-semibold transition ${settings.transition === value ? 'bg-rose-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/15'}`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            <button
              type="button"
              aria-pressed={settings.shuffle}
              onClick={() => {
                // Reorder everything after the picture on screen, which stays.
                setAnchor(order[position] ?? 0);
                setPosition(0);
                setSeed(Math.floor(Math.random() * 2 ** 31));
                update({ shuffle: !settings.shuffle });
              }}
              className={`flex min-h-11 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold transition ${settings.shuffle ? 'bg-rose-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/15'}`}
            >
              <Shuffle size={15} aria-hidden /> Shuffle
            </button>
            <button
              type="button"
              aria-pressed={settings.loop}
              onClick={() => update({ loop: !settings.loop })}
              className={`flex min-h-11 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold transition ${settings.loop ? 'bg-rose-600 text-white' : 'bg-white/10 text-white/80 hover:bg-white/15'}`}
            >
              <Repeat size={15} aria-hidden /> Loop
            </button>
          </div>
        </div>
      )}

      {/* Bottom bar */}
      <div
        className={`absolute inset-x-0 bottom-0 z-10 flex items-center justify-center gap-3 bg-gradient-to-t from-black/75 to-transparent px-3 pb-[max(1rem,var(--safe-bottom))] pt-10 transition-opacity duration-500 ${
          shown ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        data-testid="private-slideshow-controls"
      >
        <button
          type="button"
          onClick={() => {
            goTo(-1, true);
            poke();
          }}
          aria-label="Previous"
          className="flex h-12 w-12 items-center justify-center rounded-full bg-white/10 backdrop-blur hover:bg-white/20"
        >
          <ChevronLeft size={22} />
        </button>
        <button
          type="button"
          onClick={() => {
            if (ended) restart();
            else setPlaying((p) => !p);
            poke();
          }}
          aria-label={playing ? 'Pause slideshow' : 'Play slideshow'}
          data-testid="private-slideshow-play"
          className="flex h-14 w-14 items-center justify-center rounded-full bg-white text-slate-900 shadow-xl transition hover:scale-105"
        >
          {playing ? <Pause size={24} className="fill-slate-900" /> : <Play size={24} className="ml-1 fill-slate-900" />}
        </button>
        <button
          type="button"
          onClick={() => {
            goTo(1, true);
            poke();
          }}
          aria-label="Next"
          className="flex h-12 w-12 items-center justify-center rounded-full bg-white/10 backdrop-blur hover:bg-white/20"
        >
          <ChevronRight size={22} />
        </button>
        <button
          type="button"
          onClick={() => setShowSettings((s) => !s)}
          aria-label="Slideshow settings"
          aria-expanded={showSettings}
          data-testid="private-slideshow-settings-open"
          className={`absolute right-3 flex h-11 w-11 items-center justify-center rounded-full backdrop-blur hover:bg-white/20 ${showSettings ? 'bg-white/25' : 'bg-white/10'}`}
        >
          <Settings2 size={18} />
        </button>
      </div>
    </div>
  );
}

/** Decrypt the next picture while this one is up, so the change is instant. */
function Upcoming({ token, vault, item }: { token: string; vault: PrivateVault; item: GalleryItem }) {
  usePrivatePicture(token, item.blob, item.mime, vault);
  return null;
}
