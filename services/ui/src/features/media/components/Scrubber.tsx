import { useState, type KeyboardEvent } from 'react';
import { Volume1, Volume2, VolumeX } from 'lucide-react';
import { formatDuration, formatRemaining, spokenDuration } from '../format';

/** Arrow keys step this far; with Shift, `SHIFT_STEP` (UX-11). */
export const KEY_STEP = 5;
export const SHIFT_STEP = 30;

/**
 * The one position slider (UX-11): a native range input, so it works with
 * a finger, a mouse, a keyboard and a screen reader. Dragging moves only the
 * knob and a time bubble; the seek is sent once, on release (BUG-54). Arrow
 * keys step 5 s (Shift: 30 s), Home/End go to the ends, and each key commits.
 * The screen reader hears "1 minute 12 seconds of 3 minutes 30 seconds".
 */
export function Scrubber({
  position, duration, onSeek, label = 'Playback position', trackKey, times = 'remaining', className = '',
}: {
  position: number;
  duration: number;
  onSeek?: (seconds: number) => void;
  label?: string;
  /** Changes with the track; a new track drops a half-finished drag. */
  trackKey?: string;
  /** What the right-hand time shows. */
  times?: 'remaining' | 'total' | 'none';
  className?: string;
}) {
  const [drag, setDrag] = useState<{ key: string | undefined; value: number } | null>(null);
  // A drag left over from another track is ignored, not committed.
  const dragging = drag !== null && drag.key === trackKey;
  const max = Math.max(1, Math.floor(duration));
  const shown = dragging ? drag.value : Math.min(position, duration > 0 ? duration : position);
  const pct = duration > 0 ? Math.min(100, Math.max(0, (shown / duration) * 100)) : 0;

  const commit = (value = drag?.value) => {
    if (value === undefined || !dragging) return;
    setDrag(null);
    onSeek?.(Math.max(0, Math.min(duration, value)));
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const step = e.shiftKey ? SHIFT_STEP : KEY_STEP;
    const target = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? shown + step
      : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? shown - step
        : e.key === 'Home' ? 0
          : e.key === 'End' ? duration
            : null;
    if (target === null) return;
    e.preventDefault();
    // A key press is a whole gesture: seek straight away.
    setDrag(null);
    onSeek?.(Math.max(0, Math.min(duration, target)));
  };

  return (
    <div className={className}>
      <div className="relative">
        {dragging && duration > 0 ? (
          <div
            className="pointer-events-none absolute -top-8 -translate-x-1/2 rounded-lg bg-[var(--site-menu-a)] px-2 py-1 text-xs font-medium tabular-nums text-[var(--site-text)] shadow-lg"
            style={{ left: `${pct}%` }}
            aria-hidden
          >
            {formatDuration(shown)}
          </div>
        ) : null}
        <input
          type="range"
          min={0}
          max={max}
          step={1}
          value={Math.min(Math.floor(shown), max)}
          disabled={!onSeek || duration <= 0}
          onChange={(e) => setDrag({ key: trackKey, value: Number(e.target.value) })}
          onPointerUp={() => commit()}
          onKeyDown={onKeyDown}
          onBlur={() => commit()}
          aria-label={label}
          aria-valuetext={`${spokenDuration(shown)} of ${spokenDuration(duration)}`}
          className="media-range h-11 w-full cursor-pointer disabled:cursor-default"
          style={{ ['--range-fill' as string]: `${pct}%` }}
        />
      </div>
      {times !== 'none' ? (
        <div className="flex justify-between text-xs tabular-nums text-[var(--site-text-muted)]">
          <span>{formatDuration(shown)}</span>
          <span>{times === 'remaining' ? formatRemaining(shown, duration) : formatDuration(duration)}</span>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Mute and volume (UX-10): the icon shows the state (no letters), the slider
 * reads "40 percent" or "muted". Callers debounce what they send. A device
 * that reports no volume (`null`) gets an explicit unknown state instead of a
 * guessed level.
 */
export function VolumeSlider({
  volume, muted, onChange, onToggleMute, label = 'Volume', className = '',
}: {
  volume: number | null;
  muted: boolean;
  onChange: (volume: number) => void;
  onToggleMute?: () => void;
  label?: string;
  className?: string;
}) {
  // No reported volume: no slider to guess a level for, just "--".
  const unknown = volume === null;
  const level = unknown || muted ? 0 : Math.max(0, Math.min(100, Math.round(volume)));
  const Icon = unknown ? (muted ? VolumeX : Volume1) : muted || level === 0 ? VolumeX : level < 50 ? Volume1 : Volume2;
  const mute = onToggleMute ? (
    <button
      type="button"
      onClick={onToggleMute}
      aria-label={muted ? 'Unmute' : 'Mute'}
      aria-pressed={muted}
      className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl text-[var(--site-text-muted)] hover:bg-[var(--site-card-bg-hover)] hover:text-[var(--site-text)]"
    >
      <Icon size={18} aria-hidden />
    </button>
  ) : <Icon size={16} className="shrink-0 text-[var(--site-text-muted)]" aria-hidden />;

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      {mute}
      {unknown ? (
        <span className="min-w-0 flex-1 text-sm text-[var(--site-text-muted)]" aria-label={`${label} unknown`}>--</span>
      ) : (
        <input
          type="range"
          min={0}
          max={100}
          step={1}
          value={level}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label={label}
          aria-valuetext={muted ? 'muted' : `${level} percent`}
          className="media-range h-11 min-w-0 flex-1 cursor-pointer"
          style={{ ['--range-fill' as string]: `${level}%` }}
        />
      )}
    </div>
  );
}
