/**
 * The pure arithmetic behind the recordings timeline on the Cameras page: a
 * day's recordings laid along a 24 hour track under a fixed centre playhead,
 * like a recorder's playback screen. Times are epoch milliseconds throughout.
 */
import type { CameraRecording } from './cameras';

export const MINUTE_MS = 60_000;
export const HOUR_MS = 60 * MINUTE_MS;
export const DAY_MS = 24 * HOUR_MS;

/** What a block on the track stands for, and so its colour. */
export type TimelineKind = 'person' | 'vehicle' | 'animal' | 'package' | 'motion' | 'continuous';

/** Most telling first: a clip with a person and motion in it is drawn as a person. */
export const KIND_ORDER: TimelineKind[] = ['person', 'vehicle', 'animal', 'package', 'motion', 'continuous'];

export const KIND_LABELS: Record<TimelineKind, string> = {
  person: 'Person',
  vehicle: 'Vehicle',
  animal: 'Animal',
  package: 'Package',
  motion: 'Motion',
  continuous: 'Continuous',
};

/** Block colours (Tailwind classes). Continuous recording is muted; alarms and AI detections stand out. */
export const KIND_COLORS: Record<TimelineKind, string> = {
  person: 'bg-amber-400',
  vehicle: 'bg-sky-400',
  animal: 'bg-emerald-400',
  package: 'bg-purple-400',
  motion: 'bg-rose-500',
  continuous: 'bg-slate-400/45',
};

const TRIGGER_KINDS: Record<string, TimelineKind> = {
  person: 'person',
  face: 'person',
  vehicle: 'vehicle',
  animal: 'animal',
  pet: 'animal',
  dog_cat: 'animal',
  package: 'package',
  forgotten_item: 'package',
  taken_item: 'package',
  // A schedule (24/7 or a timer) records whatever happens: no event set it off.
  timer: 'continuous',
  schedule: 'continuous',
  continuous: 'continuous',
};

/** The kind one trigger names; any other alarm (motion, line crossed, a doorbell press...) is motion. */
export function triggerKind(trigger: string): TimelineKind {
  return TRIGGER_KINDS[trigger] ?? 'motion';
}

/** Every kind a recording counts as, most telling first. No trigger at all means continuous. */
export function recordingKinds(rec: Pick<CameraRecording, 'triggers'>): TimelineKind[] {
  const kinds = new Set((rec.triggers ?? []).map(triggerKind));
  if (kinds.size === 0) kinds.add('continuous');
  // A scheduled clip that also caught an event is an event clip.
  if (kinds.size > 1) kinds.delete('continuous');
  return KIND_ORDER.filter((k) => kinds.has(k));
}

/** The one kind a recording is drawn as. */
export function classifyRecording(rec: Pick<CameraRecording, 'triggers'>): TimelineKind {
  return recordingKinds(rec)[0];
}

/** A recording placed on the clock. */
export interface Segment {
  rec: CameraRecording;
  startMs: number;
  endMs: number;
  kind: TimelineKind;
  kinds: TimelineKind[];
}

/**
 * The recordings as segments in time order. A clip's length is its duration
 * when it has one (the file is that long, so seeking into it is exact), its
 * end otherwise; junk times are dropped.
 */
export function buildSegments(recordings: CameraRecording[]): Segment[] {
  const out: Segment[] = [];
  for (const rec of recordings) {
    const startMs = Date.parse(rec.start);
    if (!Number.isFinite(startMs)) continue;
    const endParsed = Date.parse(rec.end);
    let endMs = rec.duration > 0 ? startMs + rec.duration * 1000 : endParsed;
    if (!Number.isFinite(endMs) || endMs <= startMs) endMs = startMs + 1000;
    const kinds = recordingKinds(rec);
    out.push({ rec, startMs, endMs, kind: kinds[0], kinds });
  }
  return out.sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs);
}

/** The kinds present, in legend order, with how many recordings each. */
export function kindsPresent(segments: Segment[]): Array<{ kind: TimelineKind; count: number }> {
  const counts = new Map<TimelineKind, number>();
  for (const s of segments) for (const k of s.kinds) counts.set(k, (counts.get(k) ?? 0) + 1);
  return KIND_ORDER.filter((k) => counts.has(k)).map((kind) => ({ kind, count: counts.get(kind) ?? 0 }));
}

/** The segments a filter chip keeps: all of them for `all`, else those that count as that kind. */
export function filterSegments(segments: Segment[], filter: TimelineKind | 'all'): Segment[] {
  return filter === 'all' ? segments : segments.filter((s) => s.kinds.includes(filter));
}

/** The segment playing at `t`, the latest-starting one where clips overlap; null in a gap. */
export function segmentAt(segments: Segment[], t: number): Segment | null {
  let hit: Segment | null = null;
  for (const s of segments) {
    if (s.startMs > t) break;
    if (t < s.endMs) hit = s;
  }
  return hit;
}

/** The first segment that starts after `t`. */
export function nextSegment(segments: Segment[], t: number): Segment | null {
  return segments.find((s) => s.startMs > t + 500) ?? null;
}

/**
 * The segment before the one at `t` (or before `t`, in a gap): a "previous
 * event" press goes to the clip before, not back to the start of this one.
 */
export function previousSegment(segments: Segment[], t: number): Segment | null {
  const current = segmentAt(segments, t);
  const before = current ? current.startMs : t;
  let hit: Segment | null = null;
  for (const s of segments) {
    if (s.startMs >= before || s === current) break;
    hit = s;
  }
  return hit;
}

/** Where playback continues when `seg` ends: the next clip starting after it began. */
export function followingSegment(segments: Segment[], seg: Segment): Segment | null {
  const i = segments.indexOf(seg);
  if (i >= 0) return segments[i + 1] ?? null;
  return segments.find((s) => s.startMs > seg.startMs) ?? null;
}

/** What a tap at `t` plays: a segment and the second to start from, or null when nothing is near. */
export interface PlayTarget {
  seg: Segment;
  offsetS: number;
}

/**
 * Inside a clip, that clip from that moment. Just beside one (within
 * `toleranceMs`, about a finger's width), the nearest clip from its nearest
 * end. Otherwise null: the gap has no footage.
 */
export function resolvePlayTarget(segments: Segment[], t: number, toleranceMs = 0): PlayTarget | null {
  const inside = segmentAt(segments, t);
  if (inside) return { seg: inside, offsetS: Math.max(0, (t - inside.startMs) / 1000) };
  let best: Segment | null = null;
  let bestGap = Infinity;
  for (const s of segments) {
    const gap = t < s.startMs ? s.startMs - t : t - s.endMs;
    if (gap < bestGap) { best = s; bestGap = gap; }
  }
  if (!best || bestGap > toleranceMs) return null;
  const offsetS = t < best.startMs ? 0 : Math.max(0, (best.endMs - best.startMs) / 1000 - 1);
  return { seg: best, offsetS };
}

/** The zoom presets: a whole day down to ten minutes across the track. */
export const ZOOM_LEVELS: Array<{ id: string; label: string; name: string; spanMs: number }> = [
  { id: '24h', label: '24h', name: '24 hours', spanMs: DAY_MS },
  { id: '6h', label: '6h', name: '6 hours', spanMs: 6 * HOUR_MS },
  { id: '1h', label: '1h', name: '1 hour', spanMs: HOUR_MS },
  { id: '10m', label: '10m', name: '10 minutes', spanMs: 10 * MINUTE_MS },
];
export const MIN_SPAN_MS = 10 * MINUTE_MS;
export const MAX_SPAN_MS = DAY_MS;

export function clampSpan(spanMs: number): number {
  if (!Number.isFinite(spanMs)) return MAX_SPAN_MS;
  return Math.min(MAX_SPAN_MS, Math.max(MIN_SPAN_MS, spanMs));
}

/** One step in (`dir` 1) or out (-1) from `spanMs`, to the next preset. */
export function stepZoom(spanMs: number, dir: 1 | -1): number {
  const spans = ZOOM_LEVELS.map((z) => z.spanMs);
  if (dir === 1) return spans.find((s) => s < spanMs - 1) ?? MIN_SPAN_MS;
  return [...spans].reverse().find((s) => s > spanMs + 1) ?? MAX_SPAN_MS;
}

/** The time window the track shows: `spanMs` wide with the playhead in the middle. */
export interface TimeWindow {
  start: number;
  end: number;
}

export function windowAround(center: number, spanMs: number): TimeWindow {
  return { start: center - spanMs / 2, end: center + spanMs / 2 };
}

/** Pixels from the track's left edge for `t`. */
export function timeToX(t: number, win: TimeWindow, width: number): number {
  const span = win.end - win.start;
  return span > 0 ? ((t - win.start) / span) * width : 0;
}

/** The time at `x` pixels from the track's left edge. */
export function xToTime(x: number, win: TimeWindow, width: number): number {
  return width > 0 ? win.start + (x / width) * (win.end - win.start) : win.start;
}

export function clampTime(t: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, t));
}

/** A block to draw: neighbouring clips of one kind closer than a couple of pixels are one block. */
export interface Block {
  kind: TimelineKind;
  left: number;
  width: number;
  /** The first clip in the block (what a test or screen reader names it by). */
  first: Segment;
  count: number;
}

/**
 * The blocks for the visible window, in pixels. Off-screen clips are left out
 * and clips that would touch on screen are merged, so a busy day stays a few
 * dozen elements however many files it has. Every block is at least `minWidth`
 * wide, so a short clip is still something to see and tap.
 */
export function layoutBlocks(segments: Segment[], win: TimeWindow, width: number, minWidth = 3, mergeGapPx = 2): Block[] {
  const blocks: Block[] = [];
  for (const s of segments) {
    if (s.endMs < win.start || s.startMs > win.end) continue;
    const x0 = Math.max(-minWidth, timeToX(s.startMs, win, width));
    const x1 = Math.min(width + minWidth, timeToX(s.endMs, win, width));
    const left = x0;
    const right = Math.max(x1, x0 + minWidth);
    const last = blocks[blocks.length - 1];
    if (last && last.kind === s.kind && left - (last.left + last.width) <= mergeGapPx) {
      last.width = Math.max(last.width, right - last.left);
      last.count += 1;
      continue;
    }
    blocks.push({ kind: s.kind, left, width: right - left, first: s, count: 1 });
  }
  return blocks;
}

const TICK_STEPS = [MINUTE_MS, 2 * MINUTE_MS, 5 * MINUTE_MS, 10 * MINUTE_MS, 15 * MINUTE_MS, 30 * MINUTE_MS,
  HOUR_MS, 2 * HOUR_MS, 3 * HOUR_MS, 6 * HOUR_MS];

/** The smallest tick step that keeps labels at least `minGapPx` apart. */
export function tickStep(spanMs: number, width: number, minGapPx = 64): number {
  const msPerPx = width > 0 ? spanMs / width : spanMs;
  return TICK_STEPS.find((step) => step / msPerPx >= minGapPx) ?? TICK_STEPS[TICK_STEPS.length - 1];
}

/**
 * Tick times inside the window, on round local clock times (a step counted
 * from the day's midnight, so 2 h ticks fall on even hours), never outside the day.
 */
export function ticks(win: TimeWindow, step: number, dayStart: number, dayEnd: number): number[] {
  const out: number[] = [];
  const from = Math.max(win.start, dayStart);
  const to = Math.min(win.end, dayEnd);
  let t = dayStart + Math.ceil((from - dayStart) / step) * step;
  for (; t <= to && out.length < 200; t += step) out.push(t);
  return out;
}

/** How far an arrow key moves the playhead: a 24th of the view, in whole seconds. */
export function keyStep(spanMs: number): number {
  return Math.max(1000, Math.round(spanMs / 24 / 1000) * 1000);
}

/** Local midnight that starts `day`, and the next one (23 or 25 hours later across a clock change). */
export function dayBounds(day: Date): { start: number; end: number } {
  const start = new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
  const end = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1).getTime();
  return { start, end };
}

/** Where the playhead sits when a day opens: the start of its latest recording, else now (today) or noon. */
export function initialPlayhead(segments: Segment[], dayStart: number, dayEnd: number, now: number): number {
  const inDay = segments.filter((s) => s.startMs < dayEnd && s.endMs > dayStart);
  const last = inDay[inDay.length - 1];
  if (last) return clampTime(last.startMs, dayStart, dayEnd);
  if (now >= dayStart && now < dayEnd) return now;
  return dayStart + (dayEnd - dayStart) / 2;
}
