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

export interface EmojiSoundPreset {
  emoji: string;
  label: string;
  notes: Array<{ freq: number; dur: number; type?: OscillatorType }>;
}

export interface AlarmSoundOption {
  id: string;
  label: string;
  description: string;
  notes: Array<{ freq: number; dur: number; type?: OscillatorType }>;
}

export const EMOJI_PRESETS: EmojiSoundPreset[] = [
  { emoji: '🥚', label: 'Egg Timer', notes: [{ freq: 1760, dur: 0.09 }, { freq: 0, dur: 0.04 }, { freq: 1760, dur: 0.09 }, { freq: 0, dur: 0.04 }, { freq: 2093, dur: 0.25 }] },
  { emoji: '🍳', label: 'Cooking', notes: [{ freq: 523.25, dur: 0.12 }, { freq: 659.25, dur: 0.12 }, { freq: 783.99, dur: 0.12 }, { freq: 1046.5, dur: 0.3 }] },
  { emoji: '⏰', label: 'Alarm Clock', notes: [{ freq: 880, dur: 0.15, type: 'square' }, { freq: 0, dur: 0.05 }, { freq: 880, dur: 0.15, type: 'square' }, { freq: 0, dur: 0.05 }, { freq: 1174.66, dur: 0.25, type: 'square' }] },
  { emoji: '🔔', label: 'Bell', notes: [{ freq: 783.99, dur: 0.3 }, { freq: 1046.5, dur: 0.45 }] },
  { emoji: '🍕', label: 'Pizza / Oven', notes: [{ freq: 659.25, dur: 0.14 }, { freq: 783.99, dur: 0.14 }, { freq: 987.77, dur: 0.14 }, { freq: 1318.5, dur: 0.32 }] },
  { emoji: '🍝', label: 'Pasta', notes: [{ freq: 587.33, dur: 0.14 }, { freq: 739.99, dur: 0.14 }, { freq: 880, dur: 0.14 }, { freq: 1174.66, dur: 0.32 }] },
  { emoji: '☕', label: 'Coffee / Tea', notes: [{ freq: 440, dur: 0.18 }, { freq: 554.37, dur: 0.18 }, { freq: 659.25, dur: 0.35 }] },
  { emoji: '💊', label: 'Medicine', notes: [{ freq: 1046.5, dur: 0.12 }, { freq: 1318.5, dur: 0.12 }, { freq: 1046.5, dur: 0.12 }, { freq: 1567.98, dur: 0.3 }] },
  { emoji: '🏋️', label: 'Workout', notes: [{ freq: 440, dur: 0.1, type: 'triangle' }, { freq: 587.33, dur: 0.1, type: 'triangle' }, { freq: 880, dur: 0.28, type: 'triangle' }] },
  { emoji: '📚', label: 'Study / Focus', notes: [{ freq: 528, dur: 0.35 }, { freq: 660, dur: 0.45 }] },
  { emoji: '🛏️', label: 'Bedtime', notes: [{ freq: 659.25, dur: 0.22 }, { freq: 587.33, dur: 0.22 }, { freq: 523.25, dur: 0.22 }, { freq: 392, dur: 0.4 }] },
  { emoji: '🌅', label: 'Wake Up', notes: [{ freq: 440, dur: 0.16 }, { freq: 554.37, dur: 0.16 }, { freq: 659.25, dur: 0.16 }, { freq: 880, dur: 0.38 }] },
  { emoji: '🧹', label: 'Chores', notes: [{ freq: 587.33, dur: 0.12 }, { freq: 659.25, dur: 0.12 }, { freq: 783.99, dur: 0.25 }] },
  { emoji: '🚨', label: 'Urgent', notes: [{ freq: 960, dur: 0.16, type: 'sawtooth' }, { freq: 770, dur: 0.16, type: 'sawtooth' }, { freq: 960, dur: 0.16, type: 'sawtooth' }, { freq: 770, dur: 0.16, type: 'sawtooth' }] },
  { emoji: '🎉', label: 'Celebration', notes: [{ freq: 523.25, dur: 0.1 }, { freq: 659.25, dur: 0.1 }, { freq: 783.99, dur: 0.1 }, { freq: 1046.5, dur: 0.12 }, { freq: 1318.5, dur: 0.32 }] },
];

export const ALARM_SOUNDS: AlarmSoundOption[] = [
  {
    id: 'classic_alarm',
    label: 'Classic Digital Alarm',
    description: 'Crisp double-beep digital alarm clock',
    notes: [{ freq: 880, dur: 0.12, type: 'square' }, { freq: 0, dur: 0.05 }, { freq: 880, dur: 0.12, type: 'square' }, { freq: 0, dur: 0.15 }, { freq: 880, dur: 0.12, type: 'square' }, { freq: 0, dur: 0.05 }, { freq: 880, dur: 0.12, type: 'square' }],
  },
  {
    id: 'egg_timer',
    label: 'Kitchen Egg Timer',
    description: 'Rapid high-pitched kitchen timer ring',
    notes: [{ freq: 1760, dur: 0.08 }, { freq: 0, dur: 0.03 }, { freq: 1760, dur: 0.08 }, { freq: 0, dur: 0.03 }, { freq: 1760, dur: 0.08 }, { freq: 0, dur: 0.05 }, { freq: 2093, dur: 0.28 }],
  },
  {
    id: 'gentle_chime',
    label: 'Gentle Bell Chime',
    description: 'Soft two-tone ambient chime',
    notes: [{ freq: 659.25, dur: 0.28 }, { freq: 880, dur: 0.45 }],
  },
  {
    id: 'morning_melody',
    label: 'Morning Sunrise Melody',
    description: 'Warm rising major arpeggio',
    notes: [{ freq: 440, dur: 0.16 }, { freq: 554.37, dur: 0.16 }, { freq: 659.25, dur: 0.16 }, { freq: 880, dur: 0.35 }],
  },
  {
    id: 'retro_arcade',
    label: 'Retro Arcade Fanfare',
    description: '8-bit chiptune victory fanfare',
    notes: [{ freq: 523.25, dur: 0.09, type: 'square' }, { freq: 659.25, dur: 0.09, type: 'square' }, { freq: 783.99, dur: 0.09, type: 'square' }, { freq: 1046.5, dur: 0.14, type: 'square' }, { freq: 783.99, dur: 0.09, type: 'square' }, { freq: 1046.5, dur: 0.3, type: 'square' }],
  },
  {
    id: 'zen_bowl',
    label: 'Zen Singing Bowl',
    description: 'Calm harmonic meditation tone',
    notes: [{ freq: 432, dur: 0.5 }, { freq: 648, dur: 0.65 }],
  },
  {
    id: 'urgent_siren',
    label: 'Urgent Hi-Lo Alert',
    description: 'High-visibility alternating alert tone',
    notes: [{ freq: 960, dur: 0.16, type: 'sawtooth' }, { freq: 770, dur: 0.16, type: 'sawtooth' }, { freq: 960, dur: 0.16, type: 'sawtooth' }, { freq: 770, dur: 0.16, type: 'sawtooth' }],
  },
  {
    id: 'rooster',
    label: 'Rooster Crow Call',
    description: 'Playful rising wake-up call',
    notes: [{ freq: 587.33, dur: 0.12 }, { freq: 783.99, dur: 0.12 }, { freq: 987.77, dur: 0.16 }, { freq: 1174.66, dur: 0.4 }, { freq: 880, dur: 0.25 }],
  },
  {
    id: 'emoji_sound',
    label: 'Match Emoji Sound',
    description: 'Automatically plays the sound for the selected emoji',
    notes: [{ freq: 659.25, dur: 0.22 }, { freq: 880, dur: 0.35 }],
  },
  {
    id: 'tts_only',
    label: 'Spoken Voice Only (TTS)',
    description: 'Only speak the timer or alarm title out loud',
    notes: [{ freq: 659.25, dur: 0.18 }, { freq: 880, dur: 0.25 }],
  },
];

/** Play a specific alarm sound or emoji sound pattern via Web Audio. */
export function playTimerSound(sound?: string, emoji?: string): void {
  unlockChime();
  if (!ctx || ctx.state !== 'running') return;

  let notes: Array<{ freq: number; dur: number; type?: OscillatorType }> | undefined;

  if (emoji && (!sound || sound === 'emoji_sound')) {
    const preset = EMOJI_PRESETS.find((p) => p.emoji === emoji || emoji.includes(p.emoji));
    if (preset) notes = preset.notes;
  }
  if (!notes && sound) {
    const alarm = ALARM_SOUNDS.find((s) => s.id === sound);
    if (alarm) notes = alarm.notes;
  }
  if (!notes && emoji) {
    const preset = EMOJI_PRESETS.find((p) => p.emoji === emoji || emoji.includes(p.emoji));
    if (preset) notes = preset.notes;
  }
  if (!notes) {
    playChime();
    return;
  }

  let cursor = ctx.currentTime + 0.02;
  for (const step of notes) {
    if (step.freq > 0) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = step.type ?? 'sine';
      osc.frequency.setValueAtTime(step.freq, cursor);
      const peakGain = step.type === 'square' || step.type === 'sawtooth' ? 0.15 : 0.25;
      gain.gain.setValueAtTime(0.0001, cursor);
      gain.gain.exponentialRampToValueAtTime(peakGain, cursor + Math.min(0.02, step.dur * 0.25));
      gain.gain.exponentialRampToValueAtTime(0.0001, cursor + step.dur);
      osc.connect(gain).connect(ctx.destination);
      osc.start(cursor);
      osc.stop(cursor + step.dur + 0.02);
    }
    cursor += step.dur + 0.02;
  }
}

let loopInterval: ReturnType<typeof setInterval> | null = null;

/** Start a repeating alarm sound in the browser/WebView (and native Android alarm stream when available). */
export function startLoopingAlarm(sound?: string, emoji?: string, title?: string): void {
  stopLoopingAlarm();
  playTimerSound(sound, emoji);
  loopInterval = setInterval(() => {
    playTimerSound(sound, emoji);
  }, 2200);

  // Also trigger the native Android STREAM_ALARM service if running inside the APK
  void import('../plugins/chatNotify')
    .then(({ default: ChatNotify }) => {
      if (typeof ChatNotify?.ringAlarm === 'function') {
        void ChatNotify.ringAlarm({
          title: title ? `⏰ ${title}` : '⏰ Jarvis Alarm',
          body: title ? `${title} is ringing — tap Stop to silence.` : 'Tap Stop to silence.',
        }).catch(() => {});
      }
    })
    .catch(() => {});
}

/** Stop any currently looping browser or native APK alarm. */
export function stopLoopingAlarm(): void {
  if (loopInterval) {
    clearInterval(loopInterval);
    loopInterval = null;
  }
  void import('../plugins/chatNotify')
    .then(({ default: ChatNotify }) => {
      if (typeof ChatNotify?.stopAlarm === 'function') {
        void ChatNotify.stopAlarm().catch(() => {});
      }
    })
    .catch(() => {});
}

