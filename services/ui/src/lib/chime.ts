/**
 * A short, soft chime synthesised with Web Audio: no sound file, nothing
 * fetched, works the same in the browser and the Android WebView.
 *
 * Browsers only let audio start after the person has interacted with the
 * page, so the context is created (or resumed) on the first tap anywhere and
 * again when something that will chime later is set up -- a timer that fires
 * minutes from now has no gesture of its own to start sound from.
 */

type AudioContextCtor = typeof AudioContext;

let ctx: AudioContext | null = null;

function audioContextClass(): AudioContextCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as { AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

/** Make sure a running AudioContext exists. Call from a user gesture. */
export function unlockChime(): void {
  const Ctor = audioContextClass();
  if (!Ctor) return;
  try {
    if (!ctx) ctx = new Ctor();
    if (ctx.state === 'suspended') void ctx.resume().catch(() => {});
  } catch {
    // No audio available (old WebView, audio blocked): the chime is optional.
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('pointerdown', unlockChime, { once: true, capture: true });
}

/** Two rising bell tones, about a second in all. Silent if audio is unavailable. */
export function playChime(): void {
  unlockChime();
  if (!ctx || ctx.state !== 'running') return;
  const start = ctx.currentTime + 0.02;
  // E5 then A5: a bright, friendly "done" rather than an alarm.
  [659.25, 880].forEach((freq, i) => {
    const t = start + i * 0.28;
    const osc = ctx!.createOscillator();
    const gain = ctx!.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(freq, t);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.7);
    osc.connect(gain).connect(ctx!.destination);
    osc.start(t);
    osc.stop(t + 0.75);
  });
}
