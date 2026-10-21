import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Keyboard, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { useOptionalWebPlayer } from '../../context/webPlayerContext';
import { useHaptics } from '../../hooks/useHaptics';

type RepeatMode = 'off' | 'all' | 'one';

const SHORTCUTS: Array<{ keys: string[]; label: string }> = [
  { keys: ['Space'], label: 'Play or pause' },
  { keys: ['←', '→'], label: 'Seek 5 seconds (Shift: 30 seconds)' },
  { keys: ['↑', '↓'], label: 'Volume up or down by 5%' },
  { keys: ['M'], label: 'Mute or unmute' },
  { keys: ['N'], label: 'Next track' },
  { keys: ['P'], label: 'Previous track' },
  { keys: ['S'], label: 'Shuffle on or off' },
  { keys: ['R'], label: 'Cycle repeat: off, all, one' },
  { keys: ['Q'], label: 'Open the queue' },
  { keys: ['F'], label: 'Open Now Playing' },
  { keys: ['/'], label: 'Search pages and settings' },
  { keys: ['?'], label: 'Show this help' },
];

function nextRepeat(mode: RepeatMode): RepeatMode {
  if (mode === 'off') return 'all';
  if (mode === 'all') return 'one';
  return 'off';
}

function isTypingTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element || typeof element.tagName !== 'string') return false;
  const tag = element.tagName.toLowerCase();
  return (
    element.isContentEditable
    || tag === 'input'
    || tag === 'textarea'
    || tag === 'select'
    || tag === 'option'
  );
}

/** Space and Enter belong to a focused button/link before they belong to playback. */
function isActivatableTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element) return false;
  if (element.isContentEditable) return true;
  return typeof element.closest === 'function'
    && Boolean(element.closest('button, a, [role="button"], [role="link"]'));
}

function helpDialog(reduceMotion: boolean, onClose: () => void) {
  return (
    <motion.div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm sm:items-center sm:p-6"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reduceMotion ? 0 : 0.18 }}
      onClick={onClose}
    >
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        data-testid="shortcut-help"
        onClick={(event) => event.stopPropagation()}
        initial={{ y: reduceMotion ? 0 : 32, opacity: reduceMotion ? 0 : 1 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: reduceMotion ? 0 : 32, opacity: 0 }}
        transition={reduceMotion ? { duration: 0 } : { type: 'spring', stiffness: 400, damping: 40 }}
        className="w-full max-w-md overflow-hidden rounded-t-3xl border border-white/10 bg-slate-900/95 shadow-2xl shadow-black/60 backdrop-blur-xl safe-area-bottom sm:rounded-3xl"
      >
        <header className="flex items-center justify-between border-b border-white/10 px-4 py-2">
          <div className="flex items-center gap-2">
            <Keyboard size={17} className="text-cyan-300" aria-hidden />
            <h2 className="text-base font-semibold text-white">Keyboard shortcuts</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close keyboard shortcuts"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-white"
          >
            <X size={19} />
          </button>
        </header>
        <dl className="max-h-[70dvh] space-y-1 overflow-y-auto px-4 py-3">
          {SHORTCUTS.map((shortcut) => (
            <div key={shortcut.label} className="flex items-center justify-between gap-4 rounded-lg px-1 py-1.5">
              <dt className="flex shrink-0 items-center gap-1">
                {shortcut.keys.map((key) => (
                  <kbd
                    key={key}
                    className="min-w-7 rounded-md border border-white/15 bg-white/5 px-1.5 py-0.5 text-center text-[11px] font-semibold text-slate-200"
                  >
                    {key}
                  </kbd>
                ))}
              </dt>
              <dd className="text-right text-xs text-slate-400">{shortcut.label}</dd>
            </div>
          ))}
        </dl>
        <p className="border-t border-white/10 px-4 py-2 text-[11px] text-slate-500">
          Shortcuts are off while you are typing in a field.
        </p>
      </motion.div>
    </motion.div>
  );
}

/**
 * Desktop keyboard control for the shared web player.
 *
 * Mounted once above the router. Every handler reads the live player through a
 * ref, so the listener is attached once and never steals a key from a text field.
 */
export default function MediaShortcuts({
  onOpenNowPlaying,
  onOpenQueue,
}: {
  onOpenNowPlaying: () => void;
  onOpenQueue: () => void;
}) {
  const player = useOptionalWebPlayer();
  const { trigger } = useHaptics();
  const reduceMotion = useReducedMotion();
  const [helpOpen, setHelpOpen] = useState(false);

  const playerRef = useRef(player);
  const helpRef = useRef(helpOpen);
  const openNowRef = useRef(onOpenNowPlaying);
  const openQueueRef = useRef(onOpenQueue);
  const triggerRef = useRef(trigger);
  useEffect(() => {
    playerRef.current = player;
    helpRef.current = helpOpen;
    openNowRef.current = onOpenNowPlaying;
    openQueueRef.current = onOpenQueue;
    triggerRef.current = trigger;
  });

  useEffect(() => {
    if (!helpOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [helpOpen]);

  useEffect(() => {
    if (!helpOpen) return;
    // The help dialog is the topmost layer. Capture at the window so Escape
    // closes only the help, not a now-playing sheet or queue underneath it.
    const onKey = (event: KeyboardEvent) => {
      event.stopPropagation();
      if (event.key === 'Escape' || event.key === '?') {
        event.preventDefault();
        setHelpOpen(false);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [helpOpen]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
      if (isTypingTarget(event.target)) return;

      if (helpRef.current && event.key === 'Escape') {
        event.preventDefault();
        setHelpOpen(false);
        return;
      }
      if (helpRef.current) return;

      if (event.key === '/') {
        const search = document.querySelector<HTMLInputElement>('[data-global-search]');
        if (!search) return;
        event.preventDefault();
        search.focus();
        search.select();
        return;
      }

      if (event.key === '?') {
        event.preventDefault();
        setHelpOpen(true);
        return;
      }

      const active = playerRef.current;
      const isSpace = event.key === ' ' || event.code === 'Space';
      if (isSpace && isActivatableTarget(event.target)) return;

      // No current item means there is nothing to control. Without this guard
      // Space on any page would wake the web player and resume its old queue.
      if (!active?.mediaTitle) return;

      const run = (action: () => void) => {
        event.preventDefault();
        triggerRef.current('light');
        action();
      };

      if (isSpace) {
        run(() => void (active.isPlaying ? active.pause() : active.play()));
        return;
      }

      switch (event.key) {
        case 'ArrowLeft':
        case 'ArrowRight': {
          const step = event.shiftKey ? 30 : 5;
          const delta = event.key === 'ArrowRight' ? step : -step;
          run(() => void active.seek(Math.min(active.duration, Math.max(0, active.position + delta))));
          return;
        }
        case 'ArrowUp':
        case 'ArrowDown': {
          const delta = event.key === 'ArrowUp' ? 5 : -5;
          run(() => {
            active.setVolume(Math.min(100, Math.max(0, active.volume + delta)));
            if (active.muted) active.setMuted(false);
          });
          return;
        }
        case 'm':
        case 'M':
          run(() => active.setMuted(!active.muted));
          return;
        case 'n':
        case 'N':
          run(() => void active.next());
          return;
        case 'p':
        case 'P':
          run(() => void active.previous());
          return;
        case 's':
        case 'S':
          run(() => {
            void (async () => {
              try {
                const queue = await active.maCommand('player_queues/get', { queue_id: active.playerId });
                const record = queue && typeof queue === 'object' ? queue as Record<string, unknown> : {};
                await active.maCommand('player_queues/shuffle', {
                  queue_id: active.playerId,
                  shuffle_enabled: !record.shuffle_enabled,
                });
              } catch {
                toast.error('Could not change shuffle');
              }
            })();
          });
          return;
        case 'r':
        case 'R':
          run(() => {
            void (async () => {
              try {
                const queue = await active.maCommand('player_queues/get', { queue_id: active.playerId });
                const record = queue && typeof queue === 'object' ? queue as Record<string, unknown> : {};
                const mode = record.repeat_mode;
                const current: RepeatMode = mode === 'all' || mode === 'one' ? mode : 'off';
                await active.maCommand('player_queues/repeat', {
                  queue_id: active.playerId,
                  repeat_mode: nextRepeat(current),
                });
              } catch {
                toast.error('Could not change repeat');
              }
            })();
          });
          return;
        case 'q':
        case 'Q':
          run(() => openQueueRef.current());
          return;
        case 'f':
        case 'F':
          run(() => openNowRef.current());
          return;
        default:
      }
    };

    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  return (
    <AnimatePresence>
      {helpOpen ? helpDialog(Boolean(reduceMotion), () => setHelpOpen(false)) : null}
    </AnimatePresence>
  );
}
