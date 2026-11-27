/**
 * Jarvis's face, as pure arithmetic.
 *
 * The mascot itself is fixed: `services/ui/android/app/src/main/res/drawable/
 * ic_launcher_foreground.xml` draws the head, and everything here only moves
 * parts of it - eyes, ears, crest, cheeks, mouth. Keeping the expressions in a
 * React-free module means the same character can be drawn by the phone, a wall
 * tablet, a watch or an ESPHome display without any of them inventing a look.
 *
 * Two rules, learned the hard way:
 *
 * 1. Nothing here may ever read as cross or scary. There are no brows, no
 *    pupils and no narrowing eyes: the mascot is friendly and stays friendly
 *    in every state, including when it has failed.
 * 2. `faceFor` is deterministic and bounded. Given the same state, level and
 *    tick it returns the same numbers, and every number stays inside its
 *    documented range, so a port cannot produce a face off the edge of a
 *    small screen.
 */

export type FaceState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'happy'
  | 'error'
  | 'sleeping';

/** Every state, in the order a porting table should list them. */
export const FACE_STATES = [
  'idle',
  'listening',
  'thinking',
  'speaking',
  'happy',
  'error',
  'sleeping',
] as const satisfies readonly FaceState[];

/** The number of speaking bars. Also the length of `bars`. */
export const VOICE_BARS = 5;

/** The number of thinking dots. Also the length of `dots`. */
export const THINKING_DOTS = 3;

/** A short word for what the face is doing, for accessible names. */
export const FACE_LABELS: Record<FaceState, string> = {
  idle: 'ready',
  listening: 'listening',
  thinking: 'thinking',
  speaking: 'speaking',
  happy: 'pleased',
  error: 'sorry',
  sleeping: 'resting',
};

export interface JarvisExpression {
  state: FaceState;
  /** Eye height multiplier. 1 is the mascot's own eye; 0 is shut. */
  eyeOpen: number;
  /** Pupil-free gaze shift, -1..1 across the visor. */
  lookX: number;
  /** Gaze shift, -1..1 from the top of the visor to the bottom. */
  lookY: number;
  /** How open the mouth is, 0..1. */
  mouthOpen: number;
  /** Smile depth, -1 (concerned) .. 1 (delighted). */
  mouthCurve: number;
  /** Speaking bars, `VOICE_BARS` long, 0..1 each. Empty in other states. */
  bars: number[];
  /** Thinking dots, `THINKING_DOTS` long, 0..1 each. Empty in other states. */
  dots: number[];
  /** Ambient glow behind the head, 0..1. */
  glow: number;
  /** Side accents (the two "ears"), 0..1. */
  ear: number;
  /** The lightning-bolt crest, 0..1. */
  crest: number;
  /** Cheek warmth, 0..1. */
  cheek: number;
  /** Whole-head tilt in degrees. */
  tilt: number;
  /** Breathing offset, -0.05..0.05 of the head's height. */
  bob: number;
  /** 1 draws the happy `^ ^` eyes instead of the oval ones. */
  squint: number;
  /** Which light the character is speaking with. */
  accent: 'accent' | 'amber';
  label: string;
}

const clamp01 = (value: number): number => {
  if (!Number.isFinite(value)) return 0;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
};

const clamp = (value: number, low: number, high: number): number =>
  value < low ? low : value > high ? high : value;

/** Milliseconds to seconds, because the constants read as rhythms. */
const seconds = (tickMs: number): number => (Number.isFinite(tickMs) ? tickMs : 0) / 1000;

/** A slow in-and-out between 0 and 1, on a period in seconds. */
const breathe = (tickMs: number, periodS: number): number =>
  (Math.sin((seconds(tickMs) / periodS) * Math.PI * 2) + 1) / 2;

/** A microphone level of 0..100 as 0..1. Anything odd reads as silence. */
export function levelToUnit(level: number): number {
  return clamp01(level / 100);
}

/**
 * 0..1 blink amount for a tick.
 *
 * A blink takes 140 ms of every 4.2 s and is a smooth in-and-out, not a switch:
 * a lid that snaps shut and open reads as a broken picture rather than a face.
 */
export function blinkAt(tickMs: number, periodMs = 4200, durationMs = 140): number {
  const t = Number.isFinite(tickMs) ? tickMs : 0;
  const phase = ((t % periodMs) + periodMs) % periodMs;
  const start = periodMs - durationMs;
  if (phase < start) return 0;
  return Math.sin(((phase - start) / durationMs) * Math.PI);
}

/** One speaking bar, 0..1, from a tick and the bar's index. */
export function voiceBar(tickMs: number, index: number): number {
  const t = seconds(tickMs);
  const wave =
    Math.sin(t * 5.3 + index * 1.7) + 0.6 * Math.sin(t * 11.1 + index * 3.1);
  return clamp01(Math.abs(wave) / 1.6);
}

/** One thinking dot, 0..1, rolling around the mouth like a small clock. */
export function thinkingDot(tickMs: number, index: number): number {
  const t = seconds(tickMs);
  const wave = (Math.sin(t * 3.4 - index * 0.9) + 1) / 2;
  return clamp01(0.3 + 0.7 * wave ** 3);
}

/**
 * The expression for a state, a microphone level and a tick.
 *
 * `level` is 0..100 as measured by the caller; every state reads it as 0..1.
 */
export function faceFor(state: FaceState, level = 0, tickMs = 0): JarvisExpression {
  const loud = levelToUnit(level);
  const blink = blinkAt(tickMs);
  const t = seconds(tickMs);

  const base: JarvisExpression = {
    state,
    eyeOpen: 1,
    lookX: 0,
    lookY: 0,
    mouthOpen: 0.1,
    mouthCurve: 0.55,
    bars: [],
    dots: [],
    glow: 0.35,
    ear: 0.25,
    crest: 0.5,
    cheek: 0.3,
    tilt: 0,
    bob: 0,
    squint: 0,
    accent: 'accent',
    label: FACE_LABELS[state],
  };

  switch (state) {
    case 'idle':
      return {
        ...base,
        eyeOpen: 1 - blink,
        bob: (breathe(tickMs, 5) - 0.5) * 0.06,
        crest: 0.45 + breathe(tickMs, 3.4) * 0.25,
        ear: 0.22 + breathe(tickMs, 4.2) * 0.12,
        glow: 0.3 + breathe(tickMs, 6) * 0.1,
      };

    case 'listening':
      // Wide and attentive, but still blinking: a face that stops blinking
      // reads as a frozen picture rather than as attention.
      return {
        ...base,
        eyeOpen: 1.12 * (1 - blink * 0.9),
        lookY: 0.1,
        ear: 0.4 + loud * 0.6,
        crest: 0.5 + loud * 0.5,
        glow: 0.45 + loud * 0.45,
        mouthOpen: 0.12 + loud * 0.45,
        mouthCurve: 0.42,
      };

    case 'thinking':
      return {
        ...base,
        eyeOpen: 0.82,
        lookX: Math.sin(t * 1.3) * 0.7,
        lookY: 0.15,
        dots: Array.from({ length: THINKING_DOTS }, (_, i) => thinkingDot(tickMs, i)),
        crest: 0.45 + breathe(tickMs, 2.6) * 0.35,
        ear: 0.3,
        mouthCurve: 0.2,
        mouthOpen: 0,
        tilt: Math.sin(t * 0.8) * 1.6,
      };

    case 'speaking': {
      const bars = Array.from({ length: VOICE_BARS }, (_, i) =>
        Math.max(0.12, voiceBar(tickMs, i)),
      );
      const mean = bars.reduce((sum, bar) => sum + bar, 0) / bars.length;
      return {
        ...base,
        eyeOpen: 0.98 * (1 - blink),
        bars,
        mouthOpen: mean,
        mouthCurve: 0.6,
        ear: 0.4 + mean * 0.6,
        crest: 0.5 + mean * 0.5,
        cheek: 0.45 + mean * 0.25,
        glow: 0.5 + mean * 0.35,
        bob: Math.sin(t * 2) * 0.04,
        tilt: Math.sin(t * 1) * 1.2,
      };
    }

    case 'happy':
      return {
        ...base,
        eyeOpen: 0.34,
        squint: 1,
        mouthCurve: 1,
        cheek: 0.9,
        crest: 1,
        ear: 0.7,
        glow: 0.7,
      };

    case 'error':
      // Apologetic, never annoyed: a small voice, a gentle tilt, the crest
      // lit amber. There is no brow to draw, and no squint.
      return {
        ...base,
        eyeOpen: 0.88,
        lookY: -0.15,
        mouthCurve: -0.35,
        mouthOpen: 0.1,
        cheek: 0.15,
        crest: 0.35,
        ear: 0.3,
        glow: 0.3,
        tilt: -5,
        accent: 'amber',
      };

    case 'sleeping':
      return {
        ...base,
        eyeOpen: 0,
        mouthCurve: 0.3,
        cheek: 0.12,
        crest: 0.12,
        ear: 0.06,
        glow: 0.12,
        bob: (breathe(tickMs, 7) - 0.5) * 0.05,
      };

    default:
      return base;
  }
}

/**
 * Which face to draw, given the turn's state and an optional short reaction.
 *
 * A wrong answer or a failure must be allowed to interrupt a cheerful flourish,
 * but a "pleased" flash must never cover up listening, thinking or speaking.
 */
export function faceStateFor(
  state: FaceState,
  reaction: 'happy' | 'error' | null = null,
): FaceState {
  if (reaction === 'error') return 'error';
  if (state === 'listening' || state === 'thinking' || state === 'speaking') return state;
  if (reaction === 'happy') return 'happy';
  return state;
}

/** Clamp a finished expression into its documented ranges. Pure, for ports. */
export function bounded(expression: JarvisExpression): JarvisExpression {
  return {
    ...expression,
    eyeOpen: clamp(expression.eyeOpen, 0, 1.2),
    lookX: clamp(expression.lookX, -1, 1),
    lookY: clamp(expression.lookY, -1, 1),
    mouthOpen: clamp01(expression.mouthOpen),
    mouthCurve: clamp(expression.mouthCurve, -1, 1),
    bars: expression.bars.map(clamp01),
    dots: expression.dots.map(clamp01),
    glow: clamp01(expression.glow),
    ear: clamp01(expression.ear),
    crest: clamp01(expression.crest),
    cheek: clamp01(expression.cheek),
    tilt: clamp(expression.tilt, -12, 12),
    bob: clamp(expression.bob, -0.05, 0.05),
    squint: clamp01(expression.squint),
  };
}
