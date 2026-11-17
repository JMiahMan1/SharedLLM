import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react';

/**
 * The detail's big slider (brightness, colour temperature, volume, position): a 56 px bar a thumb
 * can find, like the more-info dialog in Home Assistant and the Control Centre sliders on a phone.
 * It draws the value while it is dragged and sends it once, when the finger lifts (or a key is
 * released), so a drag is one command, not fifty.
 *
 * `fill` draws a filled bar in that colour; `track` draws a gradient with a marker instead (colour
 * temperature has no "more").
 */
export default function BigSlider({
  label, ariaLabel, value, min, max, step = 1, format, onCommit, disabled, fill, track, testId,
}: {
  label: string;
  /** The screen reader's name when the visible label is too short ("Reading lamp brightness"). */
  ariaLabel?: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  format: (v: number) => string;
  onCommit: (v: number) => void;
  disabled?: boolean;
  fill?: string;
  track?: string;
  testId?: string;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const bar = useRef<HTMLDivElement | null>(null);
  const keyTimer = useRef<number | null>(null);
  useEffect(() => () => { if (keyTimer.current) window.clearTimeout(keyTimer.current); }, []);
  const shown = draft ?? value;
  const span = max - min || 1;
  const ratio = Math.max(0, Math.min(1, (shown - min) / span));

  const snap = (v: number) => Math.max(min, Math.min(max, Math.round((v - min) / step) * step + min));
  const at = (clientX: number) => {
    const r = bar.current?.getBoundingClientRect();
    if (!r || r.width === 0) return shown;
    return snap(min + ((clientX - r.left) / r.width) * span);
  };
  const commit = (v: number | null) => {
    setDraft(null);
    if (v != null && v !== value) onCommit(v);
  };

  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setDraft(at(e.clientX));
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (disabled || draft == null) return;
    setDraft(at(e.clientX));
  };
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    if (disabled || draft == null) return;
    commit(at(e.clientX));
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    const big = Math.max(step, Math.round(span / 10 / step) * step);
    const moves: Record<string, number> = {
      ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step, PageUp: big, PageDown: -big,
    };
    let next: number | null = null;
    if (e.key in moves) next = snap(shown + moves[e.key]);
    else if (e.key === 'Home') next = min;
    else if (e.key === 'End') next = max;
    if (next == null) return;
    e.preventDefault();
    setDraft(next);
    // Several key presses in a row are one change.
    if (keyTimer.current) window.clearTimeout(keyTimer.current);
    const v = next;
    keyTimer.current = window.setTimeout(() => { keyTimer.current = null; commit(v); }, 450);
  };

  const style: CSSProperties = track ? { backgroundImage: track } : {};
  // The label and value sit above the bar, never on it: on the bar they collided with the white
  // marker and straddled the fill's edge at low values.
  return (
    <div className="w-full min-w-0">
    <div className="mb-1.5 flex items-baseline justify-between gap-3 px-1 text-sm" aria-hidden>
      <span className="truncate font-medium text-slate-300">{label}</span>
      <span className="shrink-0 font-semibold tabular-nums text-white" data-testid={testId ? `${testId}-value` : undefined}>{format(shown)}</span>
    </div>
    <div
      ref={bar}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={ariaLabel ?? label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(shown)}
      aria-valuetext={format(shown)}
      aria-disabled={disabled || undefined}
      data-testid={testId}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={() => setDraft(null)}
      onKeyDown={onKey}
      style={style}
      className={`relative h-12 w-full touch-none select-none overflow-hidden rounded-2xl border border-white/10 outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${
        track ? '' : 'bg-white/[0.06]'} ${disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}`}
    >
      {fill && !track && (
        <div className="absolute inset-y-0 left-0 rounded-2xl motion-safe:transition-[width] motion-safe:duration-150"
          style={{ width: `${Math.max(ratio * 100, 4)}%`, backgroundColor: fill }} aria-hidden />
      )}
      {track && (
        <div className="absolute inset-y-1 w-2 -translate-x-1/2 rounded-full bg-white shadow-[0_0_0_2px_rgba(0,0,0,0.4)]"
          style={{ left: `${Math.min(97, Math.max(3, ratio * 100))}%` }} aria-hidden />
      )}
    </div>
    </div>
  );
}
