import { describe, expect, it } from 'vitest';
import {
  FACE_LABELS,
  FACE_STATES,
  faceFor,
  faceStateFor,
  levelToUnit,
  thinkingDot,
  voiceBar,
  VOICE_BARS,
  THINKING_DOTS,
  type JarvisExpression,
} from './jarvisFace';

const numeric = (expression: JarvisExpression): number[] => [
  expression.eyeOpen,
  expression.lookX,
  expression.lookY,
  expression.mouthOpen,
  expression.mouthCurve,
  expression.glow,
  expression.ear,
  expression.crest,
  expression.cheek,
  expression.tilt,
  expression.bob,
  expression.squint,
  ...expression.bars,
  ...expression.dots,
];

const TICKS = [0, 500, 2100, 4200, 12345];

describe('jarvisFace', () => {
  it('names every state', () => {
    for (const state of FACE_STATES) {
      expect(FACE_LABELS[state]).toBeTruthy();
      expect(faceFor(state).label).toBe(FACE_LABELS[state]);
      expect(faceFor(state).state).toBe(state);
    }
  });

  it('is pure: the same inputs give the same face', () => {
    for (const state of FACE_STATES) {
      expect(faceFor(state, 42, 900)).toEqual(faceFor(state, 42, 900));
    }
  });

  it('keeps every number inside its range, at every tick', () => {
    for (const state of FACE_STATES) {
      for (const tick of TICKS) {
        for (const value of numeric(faceFor(state, 100, tick))) {
          expect(Number.isFinite(value)).toBe(true);
          expect(Math.abs(value)).toBeLessThanOrEqual(12);
        }
        const face = faceFor(state, 100, tick);
        expect(face.eyeOpen).toBeGreaterThanOrEqual(0);
        expect(face.eyeOpen).toBeLessThanOrEqual(1.2);
        expect(face.lookX).toBeGreaterThanOrEqual(-1);
        expect(face.lookX).toBeLessThanOrEqual(1);
        expect(face.mouthCurve).toBeGreaterThanOrEqual(-1);
        expect(face.mouthCurve).toBeLessThanOrEqual(1);
        for (const key of ['mouthOpen', 'glow', 'ear', 'crest', 'cheek', 'squint'] as const) {
          expect(face[key]).toBeGreaterThanOrEqual(0);
          expect(face[key]).toBeLessThanOrEqual(1);
        }
        expect(Math.abs(face.bob)).toBeLessThanOrEqual(0.05);
      }
    }
  });

  it('blinks for 140ms of every 4.2s, smoothly', () => {
    expect(faceFor('idle', 0, 0).eyeOpen).toBeCloseTo(1, 2);
    expect(faceFor('idle', 0, 2000).eyeOpen).toBeCloseTo(1, 2);
    expect(faceFor('idle', 0, 4060).eyeOpen).toBeCloseTo(1, 2);
    // mid-blink, the lid is nearly shut
    expect(faceFor('idle', 0, 4130).eyeOpen).toBeLessThan(0.2);
    expect(faceFor('idle', 0, 4200).eyeOpen).toBeCloseTo(1, 2);
  });

  it('idles with a breath, a shimmering crest and no voice', () => {
    const early = faceFor('idle', 0, 0);
    // a quarter of the breath, so the chest really is in a different place
    const later = faceFor('idle', 0, 1250);
    expect(early.bars).toHaveLength(0);
    expect(early.dots).toHaveLength(0);
    expect(early.bob).not.toBeCloseTo(later.bob, 3);
    expect(early.crest).not.toBeCloseTo(later.crest, 3);
  });

  it('listens with wide eyes that still blink, and ears that answer the room', () => {
    const quiet = faceFor('listening', 0, 500);
    const loud = faceFor('listening', 95, 500);
    expect(quiet.eyeOpen).toBeGreaterThan(1);
    expect(loud.ear).toBeGreaterThan(quiet.ear);
    expect(loud.crest).toBeGreaterThan(quiet.crest);
    expect(loud.glow).toBeGreaterThan(quiet.glow);
    expect(loud.mouthOpen).toBeGreaterThan(quiet.mouthOpen);
    // A face that stops blinking reads as a frozen picture, so listening
    // keeps the blink.
    expect(faceFor('listening', 0, 4130).eyeOpen).toBeLessThan(0.25);
  });

  it('thinks by glancing and rolling three dots, never by frowning', () => {
    const a = faceFor('thinking', 0, 0);
    const b = faceFor('thinking', 0, 2200);
    expect(a.dots).toHaveLength(THINKING_DOTS);
    expect(a.bars).toHaveLength(0);
    expect(a.lookX).not.toBeCloseTo(b.lookX, 3);
    expect(a.mouthCurve).toBeGreaterThanOrEqual(0);
    const spread =
      Math.max(...a.dots) - Math.min(...a.dots) +
      (Math.max(...b.dots) - Math.min(...b.dots));
    expect(spread).toBeGreaterThan(0.3);
  });

  it('speaks with five bars that move', () => {
    const a = faceFor('speaking', 0, 100);
    const b = faceFor('speaking', 0, 260);
    expect(a.bars).toHaveLength(VOICE_BARS);
    expect(a.bars.every((bar) => bar > 0)).toBe(true);
    expect(a.bars).not.toEqual(b.bars);
    expect(a.mouthOpen).toBeGreaterThan(0.1);
    // speaking blinks as well
    expect(faceFor('speaking', 0, 4130).eyeOpen).toBeLessThan(0.25);
  });

  it('is delighted when happy', () => {
    const happy = faceFor('happy');
    expect(happy.squint).toBe(1);
    expect(happy.mouthCurve).toBe(1);
    expect(happy.cheek).toBeGreaterThan(0.8);
    expect(happy.crest).toBeGreaterThan(0.9);
  });

  it('is apologetic rather than cross when something fails', () => {
    const error = faceFor('error');
    expect(error.accent).toBe('amber');
    expect(error.tilt).toBeLessThan(0);
    expect(error.mouthCurve).toBeLessThan(0);
    expect(error.squint).toBe(0);
    expect(error.bars).toHaveLength(0);
    expect(error.dots).toHaveLength(0);
  });

  it('sleeps with the lids down and the light low', () => {
    const asleep = faceFor('sleeping');
    expect(asleep.eyeOpen).toBe(0);
    expect(asleep.glow).toBeLessThan(0.2);
    expect(asleep.crest).toBeLessThan(0.2);
  });

  it('lets a reaction through only where it belongs', () => {
    expect(faceStateFor('idle', null)).toBe('idle');
    expect(faceStateFor('idle', 'happy')).toBe('happy');
    expect(faceStateFor('idle', 'error')).toBe('error');
    expect(faceStateFor('listening', 'happy')).toBe('listening');
    expect(faceStateFor('thinking', 'happy')).toBe('thinking');
    expect(faceStateFor('speaking', 'happy')).toBe('speaking');
    expect(faceStateFor('listening', 'error')).toBe('error');
  });

  it('reads a microphone level as 0..1 and shrugs off nonsense', () => {
    expect(levelToUnit(0)).toBe(0);
    expect(levelToUnit(50)).toBeCloseTo(0.5, 5);
    expect(levelToUnit(100)).toBe(1);
    expect(levelToUnit(180)).toBe(1);
    expect(levelToUnit(-20)).toBe(0);
    expect(levelToUnit(Number.NaN)).toBe(0);
  });

  it('bounds the voice bars and thinking dots', () => {
    for (const tick of TICKS) {
      for (let index = 0; index < VOICE_BARS; index += 1) {
        expect(voiceBar(tick, index)).toBeGreaterThanOrEqual(0);
        expect(voiceBar(tick, index)).toBeLessThanOrEqual(1);
      }
      for (let index = 0; index < THINKING_DOTS; index += 1) {
        expect(thinkingDot(tick, index)).toBeGreaterThanOrEqual(0);
        expect(thinkingDot(tick, index)).toBeLessThanOrEqual(1);
      }
    }
  });
});
