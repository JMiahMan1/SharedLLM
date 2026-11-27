/**
 * Jarvis's face, as numbers.
 *
 * One pure function decides how he looks, so every surface that shows him -
 * the phone's assistant sheet, the tablet on the wall, a watch, an ESPHome
 * display - can share one personality instead of each inventing its own.
 *
 * The rules:
 *   - **Deterministic.** `faceFor(state, level, tick)` is a pure function of its
 *     arguments. No `Math.random`, no `Date.now`: the same tick draws the same
 *     frame, which is what makes it testable and what lets a 1-bit OLED and a
 *     React canvas agree.
 *   - **Bounded.** Every number is 0..1 (or -1..1 where it is signed), so a
 *     renderer can map it straight onto pixels, PWM or an LCD without knowing
 *     what it means.
 *   - **Slow, not busy.** A face that is always moving is a face nobody trusts.
 *     Idle breathes once every four seconds and blinks once per cycle; only
 *     listening and speaking move at the speed of the person.
 *   - **Every light lives inside the dark visor.** The renderer draws the mouth
 *     and the voice bars on dark glass, never on the accent-coloured shell:
 *     purple lights on a purple head are invisible, which is exactly how the
 *     first version failed on a real phone.
 *
 * The hardware contract is written down in `docs/JARVIS_HEAD.md`.
 */

export type FaceState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'happy' | 'error' | 'sleeping';

export const FACE_STATES: readonly FaceState[] = [
  'idle',
  'listening',
  'thinking',
  'speaking',
  'happy',
  'error',
  'sleeping',
] as const;

/** How many voice bars a talking face draws. */
export const VOICE_BARS = 5;

/** How many dots a thinking face draws. */
export const THINKING_DOTS = 3;

export interface JarvisExpression {
  state: FaceState;
  /** 0 = lids shut, 1 = wide awake. */
  eyeOpen: number;
  /** Pupil offset, -1 (left) .. 1 (right). */
  lookX: number;
  /** Pupil offset, -1 (down) .. 1 (up). */
  lookY: number;
  /** 0 = shut, 1 = wide. */
  mouthOpen: number;
  /** -1 frown, 0 flat, 1 smile. */
  mouthCurve: number;
  /** Voice bars, `VOICE_BARS` of them, each 0..1. Empty unless speaking. */
  bars: number[];
  /** Thinking dots, `THINKING_DOTS` of them, each 0..1. Empty unless thinking. */
  dots: number[];
  /** How brightly the shell rim and visor glow, 0..1. */
  glow: number;
  /** Ear-pod light: the microphone level when listening, speech energy when speaking, 0..1. */
  pod: number;
  /** Status pip above the visor, 0..1. */
  pip: number;
  /** Brow attitude, -1 worried (inner ends up) .. 1 cross (inner ends down). 0 = unreadable. */
  brow: number;
  /** Head tilt in degrees, -12..12. */
  tilt: number;
  /** Vertical bob, -1..1 of a head unit. */
  bob: number;
  /** Happy eyes are curved shut, 0..1 (1 = full `^ ^`). */
  squint: number;
  /** Which light to use: the household accent, or amber for a fault. */
  accent: 'accent' | 'amber';
  /** One word for a screen reader or a serial log. */
  label: string;
}

/** A short label for each state, used by the DOM and by hardware logs. */
export const FACE_LABELS: Record<FaceState, string> = {
  idle: 'ready',
  listening: 'listening',
  thinking: 'thinking',
  speaking: 'speaking',
  happy: 'happy',
  error: 'trouble',
  sleeping: 'resting',
};

const TAU = Math.PI * 2;

/** Seconds into the tick, for readability below. */
const seconds = (tickMs: number) => tickMs / 1000;

/** 0..1, clamped. */
const clamp01 = (n: number) => (Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0);

/** A gentle 0..1 breath, once every `periodS` seconds. */
const breathe = (tickMs: number, periodS = 4) => (Math.sin((seconds(tickMs) / periodS) * TAU) + 1) / 2;

/** The level the microphone reported (0..100) as 0..1. */
export const levelToUnit = (level: number) => clamp01((Number.isFinite(level) ? level : 0) / 100);

/**
 * How shut the lids are this instant, 0..1.
 *
 * A blink is 140 ms of a 4.2 s cycle: long enough to read as a blink, short
 * enough that it never looks like a slow fade.
 */
export function blinkAt(tickMs: number, periodMs = 4200, durationMs = 140): number {
  const phase = ((tickMs % periodMs) + periodMs) % periodMs;
  const start = periodMs - durationMs;
  if (phase < start) return 0;
  const p = (phase - start) / durationMs;
  return Math.sin(p * Math.PI);
}

/**
 * One deterministic voice bar, 0..1.
 *
 * Two sine waves at unrelated speeds beat against each other, which reads as
 * speech rather than a metronome, and never repeats on the bar's own period.
 */
export function voiceBar(tickMs: number, index: number): number {
  const a = Math.sin(seconds(tickMs) * 5.3 + index * 1.7);
  const b = Math.sin(seconds(tickMs) * 11.1 + index * 3.1) * 0.6;
  return clamp01(Math.abs(a + b) / 1.6);
}

/**
 * One deterministic thinking dot, 0..1.
 *
 * Cubing the raised sine makes each dot sit low for most of its turn and pop
 * briefly, which reads as "working" rather than as a loading spinner.
 */
export function thinkingDot(tickMs: number, index: number): number {
  const wave = (Math.sin(seconds(tickMs) * 3.4 - index * 0.9) + 1) / 2;
  return clamp01(0.3 + 0.7 * wave ** 3);
}

/**
 * The whole face, as a pure function.
 *
 * @param state one of `FACE_STATES`
 * @param level the microphone level, 0..100
 * @param tickMs milliseconds since the face appeared (freeze it for a still frame)
 */
export function faceFor(state: FaceState, level = 0, tickMs = 0): JarvisExpression {
  const loud = levelToUnit(level);
  const blink = blinkAt(tickMs);
  const base: JarvisExpression = {
    state,
    eyeOpen: 0.92,
    lookX: 0,
    lookY: 0,
    mouthOpen: 0.06,
    mouthCurve: 0.15,
    bars: [],
    dots: [],
    glow: 0.34,
    pod: 0.12,
    pip: 0.4,
    brow: 0,
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
        // Breathing, not swaying: a tenth of a head unit.
        bob: (breathe(tickMs) - 0.5) * 0.04,
        eyeOpen: 0.92 * (1 - blink),
        glow: 0.3 + breathe(tickMs, 6) * 0.08,
        pod: 0.1 + breathe(tickMs, 5) * 0.06,
        pip: 0.35 + breathe(tickMs, 6) * 0.12,
      };

    case 'listening':
      return {
        ...base,
        // Attentive: wide and looking slightly up at the person. He still
        // blinks while listening - a face that stops blinking looks frozen,
        // and a frozen orb was the thing this replaced.
        eyeOpen: 1 - blink,
        lookY: 0.18,
        mouthOpen: 0.08 + loud * 0.22,
        mouthCurve: 0.05,
        glow: 0.45 + loud * 0.5,
        // The ear-pods are the "I can hear you" light: they follow the room.
        pod: 0.35 + loud * 0.65,
        pip: 0.85,
        bob: (breathe(tickMs, 2.4) - 0.5) * 0.02,
      };

    case 'thinking':
      return {
        ...base,
        // A glance to one side, then the other, and a visor that pulses while
        // the answer is being written.
        eyeOpen: 0.66 * (1 - blink),
        lookX: Math.sin(seconds(tickMs) * 1.4) * 0.55,
        lookY: 0.28,
        mouthOpen: 0,
        mouthCurve: -0.05,
        dots: Array.from({ length: THINKING_DOTS }, (_, i) => thinkingDot(tickMs, i)),
        glow: 0.4 + breathe(tickMs, 1.6) * 0.25,
        pod: 0.22 + breathe(tickMs, 2.2) * 0.12,
        pip: 0.6,
        // Brows raised a little: pondering, not angry.
        brow: -0.3,
        tilt: Math.sin(seconds(tickMs) * 0.9) * 2.5,
      };

    case 'speaking': {
      const bars = Array.from({ length: VOICE_BARS }, (_, i) => Math.max(0.12, voiceBar(tickMs, i)));
      const mean = bars.reduce((sum, v) => sum + v, 0) / bars.length;
      return {
        ...base,
        // He blinks through a long answer too: a wide-eyed stare for thirty
        // seconds is the uncanny thing, not the words.
        eyeOpen: 0.84 * (1 - blink),
        mouthOpen: 0.25 + mean * 0.75,
        mouthCurve: 0.2,
        bars,
        glow: 0.5 + mean * 0.4,
        // The pods pulse with the speech envelope, a beat behind the mouth.
        pod: 0.35 + mean * 0.65,
        pip: 0.9,
        // A slow nod, a third of a second out of phase with the words.
        bob: Math.sin(seconds(tickMs) * 2.2) * 0.018,
        tilt: Math.sin(seconds(tickMs) * 1.1) * 1.6,
      };
    }

    case 'happy':
      return {
        ...base,
        eyeOpen: 0.34,
        squint: 1,
        mouthOpen: 0.18,
        mouthCurve: 1,
        glow: 0.72,
        pod: 0.45 + breathe(tickMs, 1.4) * 0.25,
        pip: 1,
        bob: (breathe(tickMs, 1.4) - 0.5) * 0.05,
      };

    case 'error':
      return {
        ...base,
        eyeOpen: 0.5,
        lookY: -0.3,
        mouthOpen: 0.1,
        mouthCurve: -0.7,
        glow: 0.5,
        pod: 0.4,
        pip: 0.95,
        // Worried brows: the inner ends lift.
        brow: -0.85,
        tilt: -7,
        accent: 'amber',
      };

    case 'sleeping':
      return {
        ...base,
        eyeOpen: 0,
        mouthOpen: 0,
        mouthCurve: 0.05,
        glow: 0.1,
        pod: 0.05,
        pip: 0.1,
        bob: (breathe(tickMs, 6) - 0.5) * 0.05,
      };

    default:
      return base;
  }
}

/**
 * The state to wear, given what the assistant is doing and any brief reaction.
 *
 * A reaction is a short flourish (a smile after an answer, a worried face after
 * a failure). It never interrupts listening, thinking or speaking: the person
 * should always be able to see that they are being heard.
 */
export function faceStateFor(
  state: 'idle' | 'listening' | 'thinking' | 'speaking',
  reaction: 'happy' | 'error' | null,
): FaceState {
  if (reaction === 'error') return 'error';
  if (state === 'listening' || state === 'thinking' || state === 'speaking') return state;
  if (reaction === 'happy') return 'happy';
  return state;
}
