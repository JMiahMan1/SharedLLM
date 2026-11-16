import { describe, it, expect } from 'vitest';
import type { CameraRecording } from './cameras';
import {
  DAY_MS, HOUR_MS, MINUTE_MS, buildSegments, clampSpan, classifyRecording, dayBounds, filterSegments, followingSegment,
  initialPlayhead, keyStep, kindsPresent, layoutBlocks, nextSegment, previousSegment, recordingKinds, resolvePlayTarget,
  segmentAt, stepZoom, tickStep, ticks, timeToX, triggerKind, windowAround, xToTime,
} from './cameraTimeline';

const DAY = new Date(2026, 10, 9);
const at = (h: number, m = 0, s = 0) => new Date(2026, 10, 9, h, m, s).getTime();
const rec = (file: string, h: number, m: number, seconds: number, triggers: string[] = []): CameraRecording => ({
  file,
  start: new Date(at(h, m)).toISOString(),
  end: new Date(at(h, m) + seconds * 1000).toISOString(),
  duration: seconds,
  triggers,
});

describe('classifying recordings', () => {
  it('maps triggers to kinds, AI detections first', () => {
    expect(triggerKind('person')).toBe('person');
    expect(triggerKind('face')).toBe('person');
    expect(triggerKind('dog_cat')).toBe('animal');
    expect(triggerKind('timer')).toBe('continuous');
    expect(triggerKind('crossline')).toBe('motion');
    expect(triggerKind('something_new')).toBe('motion');
    expect(classifyRecording({ triggers: ['motion', 'person'] })).toBe('person');
    expect(recordingKinds({ triggers: ['motion', 'vehicle'] })).toEqual(['vehicle', 'motion']);
  });

  it('treats a schedule or no trigger as continuous, unless an event was caught too', () => {
    expect(classifyRecording({ triggers: [] })).toBe('continuous');
    expect(classifyRecording({ triggers: ['timer'] })).toBe('continuous');
    expect(recordingKinds({ triggers: ['timer', 'animal'] })).toEqual(['animal']);
  });
});

describe('segments', () => {
  const segs = buildSegments([
    rec('c.mp4', 14, 0, 300, ['motion']),
    rec('a.mp4', 8, 0, 60, ['person', 'motion']),
    rec('b.mp4', 9, 30, 600, ['timer']),
    { file: 'junk', start: 'nope', end: 'nope', duration: 1, triggers: [] },
  ]);

  it('sorts by start, drops junk and takes the length from the duration', () => {
    expect(segs.map((s) => s.rec.file)).toEqual(['a.mp4', 'b.mp4', 'c.mp4']);
    expect(segs[0].endMs - segs[0].startMs).toBe(60_000);
  });

  it('falls back to the end time when there is no duration', () => {
    const [s] = buildSegments([{ file: 'x', start: new Date(at(1)).toISOString(), end: new Date(at(1, 2)).toISOString(), duration: 0, triggers: [] }]);
    expect(s.endMs - s.startMs).toBe(2 * MINUTE_MS);
  });

  it('counts kinds for the filter chips and filters by any kind a clip has', () => {
    expect(kindsPresent(segs)).toEqual([
      { kind: 'person', count: 1 }, { kind: 'motion', count: 2 }, { kind: 'continuous', count: 1 },
    ]);
    expect(filterSegments(segs, 'motion').map((s) => s.rec.file)).toEqual(['a.mp4', 'c.mp4']);
    expect(filterSegments(segs, 'all')).toHaveLength(3);
  });

  it('finds the clip at a time, and none in a gap', () => {
    expect(segmentAt(segs, at(9, 35))?.rec.file).toBe('b.mp4');
    expect(segmentAt(segs, at(11))).toBeNull();
    expect(segmentAt(segs, at(8, 1))).toBeNull(); // the end is exclusive
  });

  it('steps to the next and previous clip', () => {
    expect(nextSegment(segs, at(8, 0, 30))?.rec.file).toBe('b.mp4');
    expect(nextSegment(segs, at(14, 1))).toBeNull();
    // Inside b, previous is a, not the start of b.
    expect(previousSegment(segs, at(9, 35))?.rec.file).toBe('a.mp4');
    // In a gap, previous is the clip before the gap.
    expect(previousSegment(segs, at(12))?.rec.file).toBe('b.mp4');
    expect(previousSegment(segs, at(8, 0, 30))).toBeNull();
    expect(followingSegment(segs, segs[0])?.rec.file).toBe('b.mp4');
    expect(followingSegment(segs, segs[2])).toBeNull();
  });

  it('plays a tap inside a clip from that moment, snaps a near miss, and ignores an empty gap', () => {
    expect(resolvePlayTarget(segs, at(9, 32))).toMatchObject({ seg: { rec: { file: 'b.mp4' } }, offsetS: 120 });
    expect(resolvePlayTarget(segs, at(13, 59), 2 * MINUTE_MS)).toMatchObject({ seg: { rec: { file: 'c.mp4' } }, offsetS: 0 });
    expect(resolvePlayTarget(segs, at(12), 2 * MINUTE_MS)).toBeNull();
    // Just after a clip ended: its last second.
    expect(resolvePlayTarget(segs, at(8, 1, 10), MINUTE_MS)).toMatchObject({ seg: { rec: { file: 'a.mp4' } }, offsetS: 59 });
  });
});

describe('the track', () => {
  it('maps time to pixels and back around a centred playhead', () => {
    const win = windowAround(at(12), 6 * HOUR_MS);
    expect(win).toEqual({ start: at(9), end: at(15) });
    expect(timeToX(at(12), win, 600)).toBe(300);
    expect(timeToX(at(9), win, 600)).toBe(0);
    expect(xToTime(150, win, 600)).toBe(at(10, 30));
    expect(xToTime(timeToX(at(13, 17), win, 777), win, 777)).toBeCloseTo(at(13, 17), -1);
  });

  it('zooms between the presets and clamps a pinch', () => {
    expect(stepZoom(DAY_MS, 1)).toBe(6 * HOUR_MS);
    expect(stepZoom(6 * HOUR_MS, 1)).toBe(HOUR_MS);
    expect(stepZoom(10 * MINUTE_MS, 1)).toBe(10 * MINUTE_MS);
    expect(stepZoom(HOUR_MS, -1)).toBe(6 * HOUR_MS);
    expect(stepZoom(3 * HOUR_MS, -1)).toBe(6 * HOUR_MS);
    expect(stepZoom(DAY_MS, -1)).toBe(DAY_MS);
    expect(clampSpan(1000)).toBe(10 * MINUTE_MS);
    expect(clampSpan(DAY_MS * 3)).toBe(DAY_MS);
    expect(clampSpan(Number.NaN)).toBe(DAY_MS);
  });

  it('merges clips that would touch on screen and leaves out what is off screen', () => {
    // 150 one-minute motion clips, one every 5 minutes from midnight.
    const many = buildSegments(Array.from({ length: 150 }, (_, i) => rec(`m${i}.mp4`, 0, i * 5, 60, ['motion'])));
    const day = windowAround(at(12), DAY_MS);
    const wide = layoutBlocks(many, day, 300);
    expect(wide.length).toBeLessThan(20);
    expect(wide.reduce((n, b) => n + b.count, 0)).toBe(150);
    // Zoomed to an hour at 06:00, only the clips near the window are laid out, each its own block.
    const hour = layoutBlocks(many, windowAround(at(6), HOUR_MS), 1200);
    expect(hour.length).toBeGreaterThanOrEqual(12);
    expect(hour.length).toBeLessThanOrEqual(14);
    expect(hour.every((b) => b.count === 1 && b.width >= 3)).toBe(true);
  });

  it('keeps different kinds as different blocks and gives short clips a visible width', () => {
    const segs = buildSegments([rec('p', 10, 0, 1, ['person']), rec('m', 10, 0, 1, ['motion'])]);
    const blocks = layoutBlocks(segs, windowAround(at(10), DAY_MS), 360);
    expect(blocks.map((b) => b.kind)).toEqual(['person', 'motion']);
    expect(blocks.every((b) => b.width >= 3)).toBe(true);
  });

  it('chooses round tick steps that fit, inside the day', () => {
    expect(tickStep(DAY_MS, 360)).toBe(6 * HOUR_MS);
    expect(tickStep(DAY_MS, 1200)).toBe(2 * HOUR_MS);
    expect(tickStep(10 * MINUTE_MS, 360)).toBe(2 * MINUTE_MS);
    const { start, end } = dayBounds(DAY);
    const t = ticks(windowAround(start + HOUR_MS, 6 * HOUR_MS), HOUR_MS, start, end);
    expect(t[0]).toBe(start);
    expect(t.at(-1)).toBe(start + 4 * HOUR_MS);
    expect(keyStep(DAY_MS)).toBe(HOUR_MS);
    expect(keyStep(10 * MINUTE_MS)).toBe(25_000);
  });

  it('opens a day on its latest recording, else now or noon', () => {
    const { start, end } = dayBounds(DAY);
    expect(end - start).toBe(DAY_MS);
    const segs = buildSegments([rec('a', 8, 0, 60), rec('b', 17, 30, 60)]);
    expect(initialPlayhead(segs, start, end, at(20))).toBe(at(17, 30));
    expect(initialPlayhead([], start, end, at(20))).toBe(at(20));
    expect(initialPlayhead([], start, end, end + DAY_MS)).toBe(at(12));
  });
});
