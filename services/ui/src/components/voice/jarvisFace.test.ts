import { describe, expect, it } from 'vitest';
import {
  FACE_LABELS,
  FACE_STATES,
  THINKING_DOTS,
  VOICE_BARS,
  blinkAt,
  faceFor,
  faceStateFor,
  levelToUnit,
  thinkingDot,
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
        expect(face.pod).toBeGreaterThanOrEqual(0);
        expect(face.pod).toBeLessThanOrEqual(1);
        expect(face.pip).toBeGreaterThanOrEqual(0);
        expect(face.pip).toBeLessThanOrEqual(1);
        expect(face.brow).toBeGreaterThanOrEqual(-1);
        expect(face.brow).toBeLessThanOrEqual(1);
        expect(Math.abs(face.lookX)).toBeLessThanOrEqual(1);
        expect(Math.abs(face.lookY)).toBeLessThanOrEqual(1);
        expect(Math.abs(face.bob)).toBeLessThanOrEqual(1);
        expect(Math.abs(face.tilt)).toBeLessThanOrEqual(12);
        for (const bar of face.bars) {
          expect(bar).toBeGreaterThanOrEqual(0);
          expect(bar).toBeLessThanOrEqual(1);
        }
        for (const dot of face.dots) {
          expect(dot).toBeGreaterThanOrEqual(0);
          expect(dot).toBeLessThanOrEqual(1);
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

  it('keeps blinking while he listens and while he talks', () => {
    // A face that stops blinking looks frozen, which is the bug this replaced.
    expect(faceFor('listening', 0, 4130).eyeOpen).toBeLessThan(0.2);
    expect(faceFor('listening', 0, 500).eyeOpen).toBeGreaterThan(0.95);
    expect(faceFor('speaking', 0, 4130).eyeOpen).toBeLessThan(0.2);
    expect(faceFor('speaking', 0, 500).eyeOpen).toBeGreaterThan(0.8);
  });

  it('listening looks up, widens, and answers the room', () => {
    const quiet = faceFor('listening', 0, 500);
    const loud = faceFor('listening', 90, 500);
    expect(loud.glow).toBeGreaterThan(quiet.glow);
    expect(loud.mouthOpen).toBeGreaterThan(quiet.mouthOpen);
    expect(quiet.lookY).toBeGreaterThan(0);
    expect(loud.eyeOpen).toBe(1);
  });

  it('lights its ear-pods with the room, and with its own voice', () => {
    const quiet = faceFor('listening', 0, 500);
    const loud = faceFor('listening', 95, 500);
    expect(loud.pod).toBeGreaterThan(quiet.pod);
    expect(loud.pod).toBeGreaterThan(0.9);

    const speakingA = faceFor('speaking', 0, 100);
    const speakingB = faceFor('speaking', 0, 260);
    expect(speakingA.pod).toBeGreaterThan(0.3);
    // The pods pulse with the speech envelope instead of sitting still.
    expect(Math.abs(speakingA.pod - speakingB.pod)).toBeGreaterThan(0.01);

    // A resting head is not lit up like a lamp.
    expect(faceFor('sleeping', 0, 0).pod).toBeLessThan(0.1);
  });

  it('keeps the status pip bright while it is working and dim at rest', () => {
    expect(faceFor('listening', 0, 500).pip).toBeGreaterThan(faceFor('idle', 0, 500).pip);
    expect(faceFor('speaking', 0, 500).pip).toBeGreaterThan(0.8);
    expect(faceFor('sleeping', 0, 500).pip).toBeLessThan(0.2);
  });

  it('thinking glances about and shows three pulsing dots', () => {
    const left = faceFor('thinking', 0, 0);
    const right = faceFor('thinking', 0, 2200);
    expect(left.lookX).not.toBeCloseTo(right.lookX, 2);
    expect(left.eyeOpen).toBeLessThan(0.8);
    expect(left.bars).toHaveLength(0);
    expect(left.dots).toHaveLength(THINKING_DOTS);
    // The dots run a wave, not three lamps on at once: over a cycle the three
    // separate clearly, even if any single frame happens to catch them level.
    const widest = Math.max(
      ...[0, 400, 700, 1300].map((t) => {
        const dots = faceFor('thinking', 0, t).dots;
        return Math.max(...dots) - Math.min(...dots);
      }),
    );
    expect(widest).toBeGreaterThan(0.3);
    for (let i = 0; i < THINKING_DOTS; i += 1) {
      expect(thinkingDot(900, i)).toBeGreaterThanOrEqual(0);
      expect(thinkingDot(900, i)).toBeLessThanOrEqual(1);
    }
    // Only thinking has dots.
    expect(faceFor('idle', 0, 0).dots).toHaveLength(0);
    expect(faceFor('speaking', 0, 0).dots).toHaveLength(0);
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
    expect(face.brow).toBe(0);
  });

  it('turns amber, frowns and lifts its brows when something is wrong', () => {
    const face = faceFor('error', 0, 0);
    expect(face.accent).toBe('amber');
    expect(face.mouthCurve).toBeLessThan(0);
    expect(face.tilt).toBeLessThan(0);
    // Worried, not cross: the inner ends of the brows go up.
    expect(face.brow).toBeLessThan(-0.5);
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
