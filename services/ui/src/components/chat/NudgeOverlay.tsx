import { useEffect } from 'react';
import { Heart, X } from 'lucide-react';

export interface Nudge {
  body: string;
  path: string;
}

/** How long a nudge stays on screen unless it is opened or closed first. */
const SHOWN_MS = 7000;

/**
 * A nudge from the other parent while Jarvis is open: a beating heart over the
 * whole screen, so it is not mistaken for one more message. The phone's own
 * notification plays the nudge sound and vibration at the same moment.
 */
export default function NudgeOverlay({ nudge, onOpen, onClose }: { nudge: Nudge; onOpen: () => void; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, SHOWN_MS);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  return (
    <div
      role="alertdialog"
      aria-label="Nudge"
      data-testid="nudge-overlay"
      className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative flex w-full max-w-sm flex-col items-center gap-4 rounded-3xl border border-rose-400/30 bg-slate-900/95 px-6 pb-6 pt-8 text-center shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-2 top-2 flex h-11 w-11 items-center justify-center rounded-full text-slate-400 hover:bg-white/5"
        >
          <X size={18} />
        </button>
        <span className="relative flex h-24 w-24 items-center justify-center">
          <span className="absolute inset-0 rounded-full bg-rose-500/25 motion-safe:animate-ping" aria-hidden />
          <Heart size={64} className="relative fill-rose-500 text-rose-400 motion-safe:animate-[nudge-beat_1.1s_ease-in-out_infinite]" aria-hidden />
        </span>
        <p className="min-w-0 break-words text-lg font-semibold text-rose-100">{nudge.body}</p>
        <button
          type="button"
          onClick={onOpen}
          data-testid="nudge-open"
          className="min-h-11 w-full rounded-2xl bg-rose-500/80 px-4 text-sm font-semibold text-white hover:bg-rose-500"
        >
          Open private chat
        </button>
      </div>
    </div>
  );
}
