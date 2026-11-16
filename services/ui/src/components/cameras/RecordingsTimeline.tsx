import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { ChevronsLeft, ChevronsRight } from 'lucide-react';
import { formatClock } from '../../lib/cameras';
import {
  KIND_COLORS, KIND_LABELS, ZOOM_LEVELS, clampSpan, clampTime, keyStep, layoutBlocks, nextSegment, previousSegment,
  segmentAt, stepZoom, tickStep, ticks, timeToX, windowAround, xToTime, type Segment, type TimelineKind,
} from '../../lib/cameraTimeline';

const TOUCH = 'min-h-11 min-w-11';
/** A tap this close to a clip (in pixels) plays it: a short clip is thinner than a finger. */
const TAP_TOLERANCE_PX = 12;
/** Pixels a press moves before it is a drag rather than a tap. */
const DRAG_THRESHOLD_PX = 6;
/** Arrow keys and a sideways wheel move the playhead at once and play from where it rests after this long. */
const SETTLE_MS = 350;

const tickLabel = (t: number) => {
  const d = new Date(t);
  return d.toLocaleTimeString([], d.getMinutes() === 0 ? { hour: 'numeric' } : { hour: 'numeric', minute: '2-digit' });
};
const clockAt = (t: number) => formatClock(new Date(t).toISOString());

export interface RecordingsTimelineProps {
  /** The recordings shown (already filtered), in time order. */
  segments: Segment[];
  dayStart: number;
  dayEnd: number;
  /** Now, when the day is today: a marker, and the end of what can have footage. */
  now: number | null;
  playhead: number;
  spanMs: number;
  /** The file playing, drawn highlighted. */
  playingFile: string | null;
  onSpan: (spanMs: number) => void;
  /** The playhead moves (a drag, a key): no playback change yet. */
  onScrub: (t: number) => void;
  /** Play from `t` (a tap, the end of a drag); `toleranceMs` is how near a clip counts as on it. */
  onCommit: (t: number, toleranceMs: number) => void;
}

/**
 * A day's recordings along a horizontal track under a fixed centre playhead:
 * drag the track to move through the day, tap a clip (or any moment of one) to
 * play from there, pinch or ctrl+wheel to zoom. For keyboards and screen
 * readers the track is a slider: arrows move the playhead, Page Up and Page
 * Down jump between recordings, Home and End go to the first and last, and
 * Enter plays from the playhead.
 */
export default function RecordingsTimeline({
  segments, dayStart, dayEnd, now, playhead, spanMs, playingFile, onSpan, onScrub, onCommit,
}: RecordingsTimelineProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const win = windowAround(playhead, spanMs);
  const msPerPx = width > 0 ? spanMs / width : 0;

  // The latest values, for listeners that outlive a render (wheel, the settle timer, a drag in progress).
  const live = useRef({ playhead, spanMs, msPerPx, onSpan, onScrub, onCommit, dayStart, dayEnd });
  useEffect(() => { live.current = { playhead, spanMs, msPerPx, onSpan, onScrub, onCommit, dayStart, dayEnd }; });

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return undefined;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const settle = useRef<number | null>(null);
  const settleLater = (t: number) => {
    if (settle.current) window.clearTimeout(settle.current);
    settle.current = window.setTimeout(() => {
      settle.current = null;
      live.current.onCommit(t, TAP_TOLERANCE_PX * live.current.msPerPx);
    }, SETTLE_MS);
  };
  useEffect(() => () => { if (settle.current) window.clearTimeout(settle.current); }, []);

  // Ctrl/cmd + wheel (and a trackpad pinch, which arrives as one) zooms; a sideways wheel pans.
  // Native, not React's, because React's wheel listener is passive and cannot stop the page zooming.
  useEffect(() => {
    const el = trackRef.current;
    if (!el) return undefined;
    const onWheel = (e: WheelEvent) => {
      const v = live.current;
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        v.onSpan(clampSpan(v.spanMs * Math.exp(e.deltaY * 0.01)));
      } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        const t = clampTime(v.playhead + e.deltaX * v.msPerPx, v.dayStart, v.dayEnd);
        v.onScrub(t);
        settleLater(t);
      }
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const pointers = useRef(new Map<number, number>());
  const drag = useRef<{ x0: number; t0: number; moved: boolean } | null>(null);
  const pinch = useRef<{ d0: number; span0: number } | null>(null);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    pointers.current.set(e.pointerId, e.clientX);
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer cannot be captured */ }
    if (pointers.current.size === 1) {
      drag.current = { x0: e.clientX, t0: playhead, moved: false };
    } else if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { d0: Math.max(1, Math.abs(a - b)), span0: spanMs };
      if (drag.current) drag.current.moved = true;
    }
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, e.clientX);
    if (pinch.current && pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      onSpan(clampSpan(pinch.current.span0 * (pinch.current.d0 / Math.max(1, Math.abs(a - b)))));
      return;
    }
    const d = drag.current;
    if (!d || !msPerPx) return;
    const dx = e.clientX - d.x0;
    if (!d.moved && Math.abs(dx) < DRAG_THRESHOLD_PX) return;
    d.moved = true;
    // Dragging the track left brings later times under the playhead.
    onScrub(clampTime(d.t0 - dx * msPerPx, dayStart, dayEnd));
  };

  const onPointerEnd = (e: PointerEvent<HTMLDivElement>, cancelled: boolean) => {
    if (!pointers.current.delete(e.pointerId)) return;
    if (pinch.current) {
      if (pointers.current.size < 2) pinch.current = null;
      // The finger left on the glass carries on dragging from here, without a jump.
      const rest = [...pointers.current.values()][0];
      if (rest !== undefined) drag.current = { x0: rest, t0: playhead, moved: true };
      else drag.current = { x0: e.clientX, t0: playhead, moved: true };
    }
    if (pointers.current.size > 0) return;
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    const tolerance = TAP_TOLERANCE_PX * msPerPx;
    if (!d.moved && !cancelled) {
      const rect = e.currentTarget.getBoundingClientRect();
      onCommit(clampTime(xToTime(e.clientX - rect.left, win, rect.width), dayStart, dayEnd), tolerance);
    } else {
      onCommit(playhead, tolerance);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const tolerance = TAP_TOLERANCE_PX * msPerPx;
    const step = keyStep(spanMs);
    const move = (t: number) => { const c = clampTime(t, dayStart, dayEnd); onScrub(c); settleLater(c); };
    const jump = (seg: Segment | null | undefined) => { if (seg) onCommit(seg.startMs, 0); };
    switch (e.key) {
      case 'ArrowLeft': case 'ArrowDown': move(playhead - step); break;
      case 'ArrowRight': case 'ArrowUp': move(playhead + step); break;
      case 'PageUp': jump(previousSegment(segments, playhead)); break;
      case 'PageDown': jump(nextSegment(segments, playhead)); break;
      case 'Home': jump(segments[0]); break;
      case 'End': jump(segments[segments.length - 1]); break;
      case 'Enter': case ' ': onCommit(playhead, tolerance); break;
      case '+': case '=': onSpan(stepZoom(spanMs, 1)); break;
      case '-': case '_': onSpan(stepZoom(spanMs, -1)); break;
      default: return;
    }
    e.preventDefault();
  };

  const blocks = useMemo(
    () => (width > 0 ? layoutBlocks(segments, windowAround(playhead, spanMs), width) : []),
    [segments, playhead, spanMs, width],
  );
  const step = tickStep(spanMs, width || 360);
  const tickTimes = ticks(win, step, dayStart, dayEnd);
  const x = (t: number) => timeToX(t, win, width);
  const here = segmentAt(segments, playhead);
  const hereLabel = here ? `${KIND_LABELS[here.kind]} recording` : 'no recording';
  const beforeDay = Math.max(0, Math.min(width, x(dayStart)));
  const afterDay = Math.max(0, Math.min(width, x(dayEnd)));
  const nowX = now !== null ? x(now) : null;

  return (
    <div className="space-y-1" data-testid="recordings-timeline">
      <div className="flex items-baseline justify-center gap-2 min-h-6" data-testid="timeline-readout">
        <span className="text-base font-semibold text-white tabular-nums">{clockAt(playhead)}</span>
        <span className="text-xs text-slate-400">{here ? KIND_LABELS[here.kind] : 'No recording'}</span>
      </div>
      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label="Recordings timeline"
        aria-valuemin={dayStart}
        aria-valuemax={dayEnd}
        aria-valuenow={Math.round(playhead)}
        aria-valuetext={`${clockAt(playhead)}, ${hereLabel}`}
        aria-describedby="recordings-timeline-help"
        data-testid="timeline-track"
        className="relative h-16 w-full overflow-hidden rounded-xl border border-white/10 bg-black/40 select-none cursor-grab active:cursor-grabbing outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
        style={{ touchAction: 'pan-y' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={(e) => onPointerEnd(e, false)}
        onPointerCancel={(e) => onPointerEnd(e, true)}
        onKeyDown={onKeyDown}
      >
        <span id="recordings-timeline-help" className="sr-only">
          Arrow keys move through the day, Page Up and Page Down jump between recordings, Enter plays from here,
          plus and minus zoom.
        </span>
        {/* Outside the day: no footage can be there. */}
        {beforeDay > 0 && <div className="absolute inset-y-0 left-0 bg-white/[0.04]" style={{ width: beforeDay }} aria-hidden />}
        {afterDay < width && <div className="absolute inset-y-0 right-0 bg-white/[0.04]" style={{ width: width - afterDay }} aria-hidden />}
        {/* Later today: nothing recorded yet. */}
        {nowX !== null && nowX < afterDay && (
          <div className="absolute inset-y-0 bg-white/[0.02]" style={{ left: Math.max(0, nowX), width: afterDay - Math.max(0, nowX) }} aria-hidden />
        )}
        {tickTimes.map((t) => (
          <div key={t} className="absolute inset-y-0 w-px bg-white/10" style={{ left: x(t) }} aria-hidden>
            <span className="absolute bottom-0.5 -translate-x-1/2 whitespace-nowrap text-[10px] leading-none text-slate-400 tabular-nums">
              {tickLabel(t)}
            </span>
          </div>
        ))}
        {blocks.map((b) => (
          <div
            key={`${b.first.rec.file}-${b.count}`}
            data-testid="timeline-block"
            data-kind={b.kind}
            data-file={b.first.rec.file}
            className={`absolute top-2 h-8 rounded-[3px] ${KIND_COLORS[b.kind]} ${
              playingFile && b.first.rec.file === playingFile ? 'ring-2 ring-white/90' : ''}`}
            style={{ left: b.left, width: b.width }}
            aria-hidden
          />
        ))}
        {nowX !== null && nowX >= 0 && nowX <= width && (
          <div className="absolute inset-y-0 w-0.5 bg-emerald-400/80" style={{ left: nowX }} data-testid="timeline-now" aria-hidden>
            <span className="absolute top-0.5 left-1 text-[9px] font-semibold uppercase leading-none text-emerald-300">Now</span>
          </div>
        )}
        {/* The playhead stays in the middle; the day moves under it. */}
        <div className="pointer-events-none absolute inset-y-0 left-1/2 -translate-x-1/2 w-0.5 bg-cyan-300 shadow-[0_0_6px_rgba(103,232,249,0.8)]" aria-hidden>
          <span className="absolute -top-px left-1/2 -translate-x-1/2 border-x-[5px] border-t-[6px] border-x-transparent border-t-cyan-300" />
        </div>
      </div>
    </div>
  );
}

/** Previous recording, the zoom presets, next recording: one row under the track. */
export function TimelineControls({ spanMs, onSpan, onPrevious, onNext, hasPrevious, hasNext }: {
  spanMs: number;
  onSpan: (spanMs: number) => void;
  onPrevious: () => void;
  onNext: () => void;
  hasPrevious: boolean;
  hasNext: boolean;
}) {
  const button = `${TOUCH} flex items-center justify-center rounded-xl border border-white/10 bg-white/5 text-white hover:bg-white/10 disabled:opacity-40`;
  return (
    <div className="flex items-center gap-1.5">
      <button type="button" onClick={onPrevious} disabled={!hasPrevious} className={button} aria-label="Previous recording">
        <ChevronsLeft size={18} aria-hidden />
      </button>
      <div className="flex min-w-0 flex-1 justify-center gap-1 rounded-xl bg-white/5 p-0.5" role="group" aria-label="Zoom">
        {ZOOM_LEVELS.map((z) => {
          const active = Math.abs(spanMs - z.spanMs) < 1000;
          return (
            <button
              key={z.id}
              type="button"
              onClick={() => onSpan(z.spanMs)}
              aria-pressed={active}
              aria-label={`Show ${z.name}`}
              data-testid={`timeline-zoom-${z.id}`}
              className={`min-h-11 min-w-0 flex-1 rounded-lg px-1 text-xs font-medium tabular-nums transition-colors ${
                active ? 'bg-cyan-500/25 text-cyan-100' : 'text-slate-300 hover:bg-white/10'}`}
            >
              {z.label}
            </button>
          );
        })}
      </div>
      <button type="button" onClick={onNext} disabled={!hasNext} className={button} aria-label="Next recording">
        <ChevronsRight size={18} aria-hidden />
      </button>
    </div>
  );
}

/** Type chips (only the types the day has), each with its colour: the filter and the colour key in one. */
export function TimelineFilters({ kinds, filter, onFilter, total }: {
  kinds: Array<{ kind: TimelineKind; count: number }>;
  filter: TimelineKind | 'all';
  onFilter: (filter: TimelineKind | 'all') => void;
  total: number;
}) {
  const chip = (active: boolean) =>
    `min-h-11 shrink-0 flex items-center gap-1.5 rounded-full border px-3 text-xs transition-colors ${
      active ? 'border-cyan-400/60 bg-cyan-500/20 text-cyan-50' : 'border-white/10 bg-white/5 text-slate-300 hover:bg-white/10'}`;
  return (
    <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5 [scrollbar-width:none]" role="group" aria-label="Show recordings of type">
      <button type="button" className={chip(filter === 'all')} aria-pressed={filter === 'all'} onClick={() => onFilter('all')}
        data-testid="timeline-filter-all">
        All <span className="tabular-nums text-slate-400">{total}</span>
      </button>
      {kinds.map(({ kind, count }) => (
        <button
          key={kind}
          type="button"
          className={chip(filter === kind)}
          aria-pressed={filter === kind}
          onClick={() => onFilter(filter === kind ? 'all' : kind)}
          data-testid={`timeline-filter-${kind}`}
        >
          <span className={`h-2.5 w-2.5 rounded-sm ${KIND_COLORS[kind]}`} aria-hidden />
          {KIND_LABELS[kind]} <span className="tabular-nums text-slate-400">{count}</span>
        </button>
      ))}
    </div>
  );
}
