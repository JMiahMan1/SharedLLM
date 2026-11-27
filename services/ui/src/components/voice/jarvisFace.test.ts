import { describe, expect, it } from 'vitest';
import {
  FACE_LABELS,
  FACE_STATES,
  VOICE_BARS,
  blinkAt,
  faceFor,
  faceStateFor,
  levelToUnit,
  voiceBar,
} from './jarvisFace';

describe('jarvisFace', () => {
  it('has a label for every state', () => {
    for (const state of FACE_STATES) {
      expect(FACE_LABELS[state]).toBeTruthy();
    }
  });

  it('is a pure function of its arguments', () => {
    // Two renders of the same tick must be identical: a face that drifts on its
    // own cannot be tested, and a watch and a phone would disagree.
    for (const state of FACE_STATES) {
      expect(faceFor(state, 42, 1234)).toEqual(faceFor(state, 42, 1234));
    }
  });

  it('keeps every number inside the range a renderer can map', () => {
    for (const state of FACE_STATES) {
      for (const tick of [0, 700, 4200, 12345]) {
        const face = faceFor(state, 85, tick);
        expect(face.eyeOpen).toBeGreaterThanOrEqual(0);
        expect(face.eyeOpen).toBeLessThanOrEqual(1);
        expect(face.mouthOpen).toBeGreaterThanOrEqual(0);
        expect(face.mouthOpen).toBeLessThanOrEqual(1);
        expect(face.glow).toBeGreaterThanOrEqual(0);
        expect(face.glow).toBeLessThanOrEqual(1);
        expect(Math.abs(face.lookX)).toBeLessThanOrEqual(1);
        expect(Math.abs(face.lookY)).toBeLessThanOrEqual(1);
        expect(Math.abs(face.bob)).toBeLessThanOrEqual(1);
        expect(Math.abs(face.tilt)).toBeLessThanOrEqual(12);
        for (const bar of face.bars) {
          expect(bar).toBeGreaterThanOrEqual(0);
          expect(bar).toBeLessThanOrEqual(1);
        }
      }
    }
  });

  it('blinks once a cycle, quickly, and is open the rest of the time', () => {
    expect(blinkAt(0)).toBe(0);
    expect(blinkAt(2000)).toBe(0);
    // The last 140 ms of the cycle is the blink, peaking in the middle.
    expect(blinkAt(4200 - 70)).toBeGreaterThan(0.95);
    expect(blinkAt(4200 - 140)).toBe(0);
    expect(blinkAt(4200)).toBe(0);
  });

  it('idle breathes and blinks but does not stare', () => {
    const open = faceFor('idle', 0, 2000);
    const blinking = faceFor('idle', 0, 4130);
    expect(blinking.eyeOpen).toBeLessThan(open.eyeOpen);
    expect(open.eyeOpen).toBeGreaterThan(0.85);
    expect(Math.abs(open.bob)).toBeLessThan(0.05);
    expect(open.bars).toHaveLength(0);
  });

  it('listening looks up, widens, and answers the room', () => {
    const quiet = faceFor('listening', 0, 500);
    const loud = faceFor('listening', 90, 500);
    expect(loud.glow).toBeGreaterThan(quiet.glow);
    expect(loud.mouthOpen).toBeGreaterThan(quiet.mouthOpen);
    expect(quiet.lookY).toBeGreaterThan(0);
    expect(loud.eyeOpen).toBe(1);
  });

  it('thinking glances about instead of holding a stare', () => {
    const left = faceFor('thinking', 0, 0);
    const right = faceFor('thinking', 0, 2200);
    expect(left.lookX).not.toBeCloseTo(right.lookX, 2);
    expect(left.eyeOpen).toBeLessThan(0.8);
    expect(left.bars).toHaveLength(0);
  });

  it('speaking draws five bars that move', () => {
    const a = faceFor('speaking', 0, 100);
    const b = faceFor('speaking', 0, 260);
    expect(a.bars).toHaveLength(VOICE_BARS);
    expect(a.bars.some((bar, i) => Math.abs(bar - b.bars[i]) > 0.01)).toBe(true);
    // Bars never fully close: a silent bar looks broken.
    for (const bar of a.bars) expect(bar).toBeGreaterThan(0);
    for (let i = 0; i < VOICE_BARS; i += 1) {
      expect(voiceBar(450, i)).toBeGreaterThanOrEqual(0);
      expect(voiceBar(450, i)).toBeLessThanOrEqual(1);
    }
  });

  it('smiles with the eyes when happy', () => {
    const face = faceFor('happy', 0, 0);
    expect(face.squint).toBe(1);
    expect(face.mouthCurve).toBe(1);
    expect(face.glow).toBeGreaterThan(0.6);
  });

  it('turns amber and leans away when something is wrong', () => {
    const face = faceFor('error', 0, 0);
    expect(face.accent).toBe('amber');
    expect(face.mouthCurve).toBeLessThan(0);
    expect(face.tilt).toBeLessThan(0);
  });

  it('closes his eyes when resting', () => {
    expect(faceFor('sleeping', 0, 0).eyeOpen).toBe(0);
    expect(faceFor('sleeping', 0, 0).glow).toBeLessThan(0.2);
  });

  it('does not let a reaction cover listening or thinking', () => {
    expect(faceStateFor('listening', 'happy')).toBe('listening');
    expect(faceStateFor('thinking', 'happy')).toBe('thinking');
    expect(faceStateFor('speaking', 'happy')).toBe('speaking');
    // A failure is louder than anything else: it must be seen.
    expect(faceStateFor('listening', 'error')).toBe('error');
    // The flourish belongs to the quiet between turns.
    expect(faceStateFor('idle', 'happy')).toBe('happy');
    expect(faceStateFor('idle', null)).toBe('idle');
  });

  it('reads a level the way the microphone reports it', () => {
    expect(levelToUnit(0)).toBe(0);
    expect(levelToUnit(50)).toBe(0.5);
    expect(levelToUnit(180)).toBe(1);
    expect(levelToUnit(Number.NaN)).toBe(0);
  });
});
