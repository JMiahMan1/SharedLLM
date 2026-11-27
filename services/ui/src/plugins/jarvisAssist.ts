import { Capacitor, registerPlugin } from '@capacitor/core';
import { api } from '../services/api';

/**
 * The phone's own voice assistant, as the native side sees it.
 *
 * A turn is heard, understood and answered entirely on the household's server:
 * the phone records one utterance, asks the gateway to hear it, streams the
 * answer sentence by sentence, and speaks each sentence in the signed-in
 * person's own voice. Nothing about it is cloud-bound.
 *
 * On the web (and on a phone still running an older APK, where the native
 * plugin does not exist yet) the same turn runs in the web layer: the browser
 * records the clip, `/api/voice/listen` hears it, `/api/chat` answers, and
 * `/api/voice/speak` returns the words as audio. That is deliberate - the web
 * bundle reaches every device through the OTA update, so the button works even
 * when the APK has not been replaced.
 *
 * `assist` events carry what the service is doing so the sheet can show it:
 * `{type: 'state'|'level'|'transcript'|'sentence'|'reply'|'error', text?, level?}`.
 */
export interface AssistEvent {
  type: 'state' | 'level' | 'transcript' | 'sentence' | 'reply' | 'error';
  text?: string;
  level?: number;
}

export type AssistState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface JarvisAssistPlugin {
  /** Hear one turn. `wake` keeps the service listening afterwards (hands-free). */
  start(options?: { wake?: boolean }): Promise<void>;
  stop(): Promise<void>;
  /** Cut the answer off mid-sentence. */
  stopSpeaking(): Promise<void>;
  status(): Promise<{ running: boolean; state: AssistState }>;
  /** Speak a line in this person's voice (used by the typed fallback). */
  speak(options: { text: string }): Promise<void>;
  /** The phrase that starts a turn when always-listening is on. */
  setWakePhrase(options: { phrase: string }): Promise<void>;
  /** Only the microphone permission, for the browser turn. Optional: an older APK has none. */
  requestMicrophone?(): Promise<{ granted: boolean }>;
  /** Whether this phone can make Jarvis its assistant, and whether it has. */
  assistantRole(): Promise<{ canHold: boolean; held: boolean }>;
  /** Ask Android to make Jarvis the assistant (long-press home). */
  requestAssistantRole(): Promise<void>;
  addListener(
    eventName: 'assist',
    listenerFunc: (event: AssistEvent) => void,
  ): Promise<{ remove: () => Promise<void> }>;
}

const native = registerPlugin<JarvisAssistPlugin>('JarvisAssist');

/* ------------------------------------------------------------------ web turn */

type Listener = (event: AssistEvent) => void;

const listeners = new Set<Listener>();
let recorder: MediaRecorder | null = null;
/** While the assistant screen is open it keeps listening after each answer. */
let handsFree = false;
let stream: MediaStream | null = null;
let chunks: Blob[] = [];
let audio: HTMLAudioElement | null = null;
let meter: number | null = null;
let state: AssistState = 'idle';

function emit(event: AssistEvent) {
  for (const listener of [...listeners]) listener(event);
}

function setState(next: AssistState) {
  state = next;
  emit({ type: 'state', text: next });
}

function stopMeter() {
  if (meter !== null) {
    window.clearInterval(meter);
    meter = null;
  }
}

/** Releases the microphone the moment the take ends, so the OS indicator goes. */
function releaseMic() {
  stopMeter();
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
}

async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

function playBase64(b64: string, mime: string): Promise<void> {
  return new Promise((resolve) => {
    try {
      const bytes = atob(b64);
      const buffer = new Uint8Array(bytes.length);
      for (let i = 0; i < bytes.length; i += 1) buffer[i] = bytes.charCodeAt(i);
      const url = URL.createObjectURL(new Blob([buffer], { type: mime || 'audio/wav' }));
      audio = new Audio(url);
      audio.onended = () => {
        URL.revokeObjectURL(url);
        resolve();
      };
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        resolve();
      };
      void audio.play().catch(() => resolve());
    } catch {
      resolve();
    }
  });
}

/** "Hey Jarvis, what time is it" is the question "what time is it". */
function withoutWake(text: string): string {
  const cut = text.replace(/^(hey\s+)?jarvis\b[,.!:]?\s*/i, '').trim();
  return cut || text;
}

async function finishTurn(blob: Blob) {
  try {
    setState('thinking');
    const audioB64 = await blobToBase64(blob);
    const heard = await api.voiceListen(audioB64, blob.type || 'audio/webm');
    const text = withoutWake((heard?.text || '').trim());
    if (!text) {
      emit({ type: 'error', text: "I didn't catch that." });
      setState('idle');
      return;
    }
    emit({ type: 'transcript', text });
    const answer = await api.chat(text, undefined, undefined, false);
    const reply = api.replyText(answer);
    if (!reply) {
      emit({ type: 'error', text: 'I heard you but got no answer.' });
      setState('idle');
      return;
    }
    emit({ type: 'reply', text: reply });
    setState('speaking');
    const spoken = await api.voiceSpeak(reply);
    if (spoken?.audio_b64) await playBase64(spoken.audio_b64, spoken.mime || 'audio/wav');
  } catch (error) {
    emit({ type: 'error', text: error instanceof Error ? error.message : 'That did not work.' });
  } finally {
    releaseMic();
    setState('idle');
    // Keep listening while the screen is open, so nobody has to tap again.
    if (handsFree) window.setTimeout(() => { if (handsFree) void webStart(); }, 700);
  }
}

async function webStart() {
  if (recorder && recorder.state === 'recording') {
    recorder.stop();
    return;
  }
  // A WebView will not record until Android has granted the microphone, and only
  // the native side can ask. Without this the sheet opens and hears nothing.
  try {
    if (Capacitor.isNativePlatform()) await native.requestMicrophone?.();
  } catch {
    // Ask the browser anyway; a refusal is reported below.
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    emit({ type: 'error', text: 'Jarvis needs the microphone to hear you.' });
    return;
  }
  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
    (type) => typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(type),
  );
  try {
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
  } catch {
    emit({ type: 'error', text: 'This browser cannot record audio.' });
    releaseMic();
    return;
  }
  chunks = [];
  recorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };
  recorder.onstop = () => {
    const blob = new Blob(chunks, { type: recorder?.mimeType || 'audio/webm' });
    recorder = null;
    if (blob.size < 2000) {
        // Under about a fifth of a second there is no speech in it. Posting that
        // comes back as an empty transcript, which looks like a dead button.
        releaseMic();
        setState('idle');
        emit({ type: 'error', text: 'I did not hear anything. Check the microphone permission, then try again.' });
        return;
      }
      void finishTurn(blob);
  };
  recorder.start(250);  // periodic chunks: a WebView may send nothing until the end
  setState('listening');
  // The level meter is decoration: a browser or WebView with no Web Audio must
  // still be able to record, or the button reads as dead.
  try {
    const Ctx = window.AudioContext
      || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctx) {
    const context = new Ctx();
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    context.createMediaStreamSource(stream).connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    meter = window.setInterval(() => {
      analyser.getByteFrequencyData(data);
      const level = Math.min(100, Math.round((data.reduce((sum, v) => sum + v, 0) / data.length) * 2.2));
      emit({ type: 'level', level });
    }, 100);
    }
  } catch {
    // No meter, but the turn still runs.
  }
  // A turn ends by itself: the same 15 s cap the phone's service uses, and no
  // recording is left running if the person walks away.
  window.setTimeout(() => {
    if (recorder && recorder.state === 'recording') recorder.stop();
  }, 15000);
}

const web: JarvisAssistPlugin = {
  async start() {
    handsFree = true;
    await webStart();
  },
  async stop() {
    handsFree = false;
    if (recorder && recorder.state === 'recording') recorder.stop();
    releaseMic();
    setState('idle');
  },
  async stopSpeaking() {
    audio?.pause();
    audio = null;
    if (state === 'speaking') setState('idle');
  },
  async status() {
    return { running: state !== 'idle', state };
  },
  async speak({ text }) {
    try {
      const spoken = await api.voiceSpeak(text);
      if (!spoken?.audio_b64) return;
      setState('speaking');
      await playBase64(spoken.audio_b64, spoken.mime || 'audio/wav');
    } catch {
      // A silent line is better than an unhandled rejection in a sheet.
    } finally {
      setState('idle');
    }
  },
  async setWakePhrase(options) {
    try {
      localStorage.setItem('jarvis_wake_phrase', options.phrase);
    } catch {
      // A private window cannot remember it; the default phrase still works.
    }
  },
  async assistantRole() {
    return { canHold: false, held: false };
  },
  async requestAssistantRole() {},
  async addListener(_eventName, listenerFunc) {
    listeners.add(listenerFunc);
    return {
      remove: async () => {
        listeners.delete(listenerFunc);
      },
    };
  },
};

/**
 * The native plugin when this is the packaged app, the web turn otherwise. A
 * phone that has not taken the newest APK yet simply gets the web turn - which
 * is why the microphone never silently does nothing.
 */
/**
 * The browser turn is the default everywhere, including inside the phone app.
 *
 * It records with MediaRecorder and talks to the gateway over plain HTTP, so it
 * works in every WebView and cannot take the app down the way a microphone
 * foreground service can (Android 12+ refuses to let one start from the
 * background, and the exception lands in the app's process). The native service
 * is kept for hands-free use only, and every native call is caught: a phone that
 * says no must never be able to crash the app or leave the screen blank.
 */
export const JarvisAssist: JarvisAssistPlugin = {
  start: (options) => {
    if (options?.wake && Capacitor.isNativePlatform()) {
      return native.start(options).catch(() => web.start());
    }
    return web.start();
  },
  stop: () => web.stop(),
  stopSpeaking: () => web.stopSpeaking(),
  status: () => web.status(),
  speak: (options) => web.speak(options),
  setWakePhrase: (options) =>
    Capacitor.isNativePlatform() ? native.setWakePhrase(options).catch(() => undefined) : web.setWakePhrase(options),
  assistantRole: () =>
    Capacitor.isNativePlatform()
      ? native.assistantRole().catch(() => ({ canHold: false, held: false }))
      : web.assistantRole(),
  requestAssistantRole: () =>
    Capacitor.isNativePlatform() ? native.requestAssistantRole().catch(() => undefined) : web.requestAssistantRole(),
  addListener: (eventName, listenerFunc) =>
    Capacitor.isNativePlatform() ? native.addListener(eventName, listenerFunc) : web.addListener(eventName, listenerFunc),
};
