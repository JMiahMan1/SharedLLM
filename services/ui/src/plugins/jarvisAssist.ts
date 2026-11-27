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
  /** `status` is a short diagnostic line (microphone state), so a silent sheet says why. */
  /** `partial` is the take so far, so words appear while the person is still talking. */
  type: 'state' | 'level' | 'partial' | 'transcript' | 'sentence' | 'reply' | 'error' | 'status';
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
/** Set when the sheet closes mid-take: the partial clip is dropped, not submitted. */
let discard = false;
/** While the assistant screen is open it keeps listening after each answer. */
let handsFree = false;
let stream: MediaStream | null = null;
let chunks: Blob[] = [];
let audio: HTMLAudioElement | null = null;
/** Resolves the clip's promise when the answer is cut off, so the queue moves on. */
let audioStop: (() => void) | null = null;
let meter: number | null = null;
/** Ends the take when the person stops talking; waiting out a fixed cap reads as a dead button. */
let silenceWatch: number | null = null;
/** Transcribes the take as it goes, so the sheet shows words instead of a silent orb. */
let partialWatch: number | null = null;
/** Only one partial request at a time: the server has one card to answer with. */
let partialBusy = false;
/** Bumped when a take ends, so a slow partial cannot overwrite the real transcript. */
let partialToken = 0;
/** The 15 s cap on one take. Tracked, so take #1's timer cannot end take #2. */
let hardCap: number | null = null;
/** The Web Audio context behind the level meter; a WebView only allows a handful of them. */
let meterCtx: AudioContext | null = null;
/** The hands-free restart. Tracked, so closing or reopening cancels a stale take. */
let rearmTimer: number | null = null;
/** Bumped per start: a start whose permission prompt is still open must not finish. */
let startSeq = 0;
let state: AssistState = 'idle';
/** The turn in flight, so closing the sheet stops it instead of letting it talk to an empty room. */
let turnAbort: AbortController | null = null;

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
  if (silenceWatch !== null) {
    window.clearInterval(silenceWatch);
    silenceWatch = null;
  }
  if (partialWatch !== null) {
    window.clearInterval(partialWatch);
    partialWatch = null;
  }
  if (hardCap !== null) {
    window.clearTimeout(hardCap);
    hardCap = null;
  }
  if (rearmTimer !== null) {
    window.clearTimeout(rearmTimer);
    rearmTimer = null;
  }
  if (meterCtx) {
    // An Android WebView allows only a few live contexts; leaving them open
    // made the level meter and the silence detection die after a few turns.
    const context = meterCtx;
    meterCtx = null;
    void Promise.resolve(context.close()).catch(() => undefined);
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
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      audioStop = null;
      resolve();
    };
    try {
      const bytes = atob(b64);
      const buffer = new Uint8Array(bytes.length);
      for (let i = 0; i < bytes.length; i += 1) buffer[i] = bytes.charCodeAt(i);
      const url = URL.createObjectURL(new Blob([buffer], { type: mime || 'audio/wav' }));
      audio = new Audio(url);
      audio.onended = () => {
        URL.revokeObjectURL(url);
        finish();
      };
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        finish();
      };
      // "Stop talking" pauses the clip, which fires neither event above: without
      // this the speech queue would wait forever on an answer nobody is hearing.
      audioStop = () => {
        try {
          audio?.pause();
        } catch {
          // already gone
        }
        finish();
      };
      void audio.play().catch(() => finish());
    } catch {
      finish();
    }
  });
}

/* ------------------------------------------------------- speaking in order */

/**
 * Sentences are synthesised as they arrive, but played in order.
 *
 * Each sentence's `/api/voice/speak` starts the moment its text exists, and one
 * chain plays them one after another, so sentence two's audio is already in hand
 * while sentence one is being heard. That is what makes a turn feel like the
 * Echo Dots instead of a pause followed by a monologue.
 */
let speechTail: Promise<void> = Promise.resolve();
let speechAborted = false;

function beginSpeech() {
  speechAborted = false;
  speechTail = Promise.resolve();
}

function enqueueSpeech(text: string, voice?: string | null) {
  const line = text.trim();
  if (!line || speechAborted) return;
  speechTail = speechTail.then(async () => {
    if (speechAborted) return;
    try {
      const spoken = await api.voiceSpeak(line, voice || undefined);
      if (spoken?.audio_b64 && !speechAborted) {
        await playBase64(spoken.audio_b64, spoken.mime || 'audio/wav');
      }
    } catch {
      // One sentence that cannot be spoken must not cut the answer short.
    }
  });
}

function stopSpeech() {
  speechAborted = true;
  speechTail = Promise.resolve();
  audioStop?.();
  audioStop = null;
  audio = null;
}

/** How this device names itself in the house's conversation context. */
function deviceName(): string {
  try {
    const saved = localStorage.getItem('jarvis_device_name');
    if (saved) return saved;
  } catch {
    // no storage: the platform name is enough
  }
  return Capacitor.isNativePlatform() ? 'Phone' : 'Browser';
}

/** Keeps listening after a turn when the sheet is open, so nobody has to tap again. */
function rearm() {
  if (!handsFree) return;
  if (rearmTimer !== null) window.clearTimeout(rearmTimer);
  rearmTimer = window.setTimeout(() => {
    rearmTimer = null;
    if (handsFree) void webStart();
  }, 700);
}

/** "Hey Jarvis, what time is it" is the question "what time is it". */
function withoutWake(text: string): string {
  const cut = text.replace(/^(hey\s+)?jarvis\b[,.!:]?\s*/i, '').trim();
  return cut || text;
}

/**
 * Answer one question out loud, speaking each sentence as it is written.
 *
 * Shared by the microphone turn and the typed one, so both behave the same and
 * both are cut off together when the sheet closes. The gateway streams the
 * answer as sentences (`speech_stream`); each one is synthesised immediately and
 * played in order, which is the Echo Dot's behaviour rather than "wait, then
 * read a paragraph".
 */
export async function askJarvis(text: string, userId?: string): Promise<void> {
  const question = withoutWake(text.trim());
  if (!question) return;
  turnAbort?.abort();
  const controller = new AbortController();
  turnAbort = controller;
  beginSpeech();
  emit({ type: 'transcript', text: question });
  setState('thinking');
  let answer = '';
  try {
    await api.voiceChatStream(question, {
      deviceName: deviceName(),
      userId,
      signal: controller.signal,
      onSentence: (sentence) => {
        if (state === 'thinking') setState('speaking');
        emit({ type: 'sentence', text: sentence.text });
        enqueueSpeech(sentence.text, sentence.voice);
      },
      onReply: (full) => {
        answer = full;
        emit({ type: 'reply', text: full });
      },
    });
    await speechTail;
    if (!answer) emit({ type: 'error', text: 'I heard you but got no answer.' });
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) {
      emit({ type: 'error', text: error instanceof Error ? error.message : 'That did not work.' });
    }
  } finally {
    if (turnAbort === controller) turnAbort = null;
    releaseMic();
    setState('idle');
    rearm();
  }
}

async function partialListen() {
  if (partialBusy) return;
  if (!recorder || recorder.state !== 'recording') return;
  const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
  // Under about a second there is nothing worth showing, and an empty answer
  // would wipe the words already on screen.
  if (blob.size < 6000) return;
  partialBusy = true;
  const token = partialToken;
  try {
    const audioB64 = await blobToBase64(blob);
    const heard = await api.voiceListen(audioB64, blob.type || 'audio/webm');
    const text = withoutWake((heard?.text || '').trim());
    if (token === partialToken && text) emit({ type: 'partial', text });
  } catch {
    // A partial is a convenience; the finished take still does the real work.
  } finally {
    partialBusy = false;
  }
}

async function finishTurn(blob: Blob) {
  let answered = false;
  try {
    setState('thinking');
    const audioB64 = await blobToBase64(blob);
    const heard = await api.voiceListen(audioB64, blob.type || 'audio/webm');
    const text = (heard?.text || '').trim();
    if (!withoutWake(text)) {
      emit({ type: 'error', text: "I didn't catch that. Try again after the tone." });
      return;
    }
    answered = true;
    await askJarvis(text);
  } catch (error) {
    emit({ type: 'error', text: error instanceof Error ? error.message : 'That did not work.' });
  } finally {
    releaseMic();
    if (!answered) {
      setState('idle');
      // A quiet take must not end the session: one silence used to stop
      // hands-free listening for good, which reads as "it stopped working".
      rearm();
    }
  }
}

async function webStart() {
  // A tap during the 700 ms hands-free pause must start one take, not two.
  if (rearmTimer !== null) {
    window.clearTimeout(rearmTimer);
    rearmTimer = null;
  }
  if (recorder && recorder.state === 'recording') {
    recorder.stop();
    return;
  }
  // A fresh take is a fresh question. Tapping "Ask again" while the answer is
  // still talking used to record Jarvis over the top of itself.
  stopSpeech();
  turnAbort?.abort();
  turnAbort = null;
  // Bumped by stop(): a start still waiting on a permission prompt must not come
  // back and leave a recording nobody is listening to.
  const seq = ++startSeq;
  // A WebView will not record until Android has granted the microphone, and only
  // the native side can ask. Without this the sheet opens and hears nothing.
  try {
    if (Capacitor.isNativePlatform()) await native.requestMicrophone?.();
  } catch {
    // Ask the browser anyway; a refusal is reported below.
  }
  if (seq !== startSeq) return;
  if (!navigator.mediaDevices?.getUserMedia) {
    emit({ type: 'status', text: 'microphone unavailable' });
    emit({ type: 'error', text: 'This screen cannot open a microphone. Open Jarvis on the app.' });
    return;
  }
  let opened: MediaStream;
  try {
    opened = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    if (seq !== startSeq) return;
    const name = error instanceof Error ? error.name : '';
    emit({
      type: 'status',
      text: name === 'NotAllowedError' ? 'microphone blocked' : 'microphone unavailable',
    });
    emit({
      type: 'error',
      text:
        name === 'NotAllowedError'
          ? 'Jarvis is not allowed the microphone. Allow it in Android settings, then tap the microphone again.'
          : 'Jarvis needs the microphone to hear you.',
    });
    setState('idle');
    return;
  }
  if (seq !== startSeq) {
    // The sheet closed while the microphone was opening: give it straight back.
    opened.getTracks().forEach((track) => track.stop());
    return;
  }
  stream = opened;
  emit({ type: 'status', text: 'microphone ready' });
  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
    (type) => typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(type),
  );
  let active: MediaRecorder;
  try {
    active = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
  } catch {
    emit({ type: 'error', text: 'This browser cannot record audio.' });
    releaseMic();
    return;
  }
  recorder = active;
  chunks = [];
  discard = false;
  active.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };
  active.onstop = () => {
    // Anything still in flight belongs to the take that just ended.
    partialToken += 1;
    const blob = new Blob(chunks, { type: active.mimeType || 'audio/webm' });
    if (recorder === active) recorder = null;
    if (discard) {
      // The sheet closed mid-take: that clip was never a question.
      discard = false;
      releaseMic();
      setState('idle');
      return;
    }
    if (blob.size < 2000) {
      // Under about a fifth of a second there is no speech in it. Posting that
      // comes back as an empty transcript, which looks like a dead button.
      releaseMic();
      setState('idle');
      emit({ type: 'error', text: 'I did not hear anything. Check the microphone permission, then try again.' });
      // Listening continues: one quiet take used to end hands-free for good.
      rearm();
      return;
    }
    void finishTurn(blob);
  };
  active.start(250);  // periodic chunks: a WebView may send nothing until the end
  setState('listening');
  // Live words: the take is transcribed every 1.5 s so the sheet shows the person
  // being heard. One request at a time, and only the newest take's words show.
  partialToken += 1;
  const takeToken = partialToken;
  partialWatch = window.setInterval(() => {
    if (takeToken === partialToken) void partialListen();
  }, 1500);
  // The level meter also ends the take: once the person has spoken and then
  // gone quiet there is no reason to make them wait out a timer, and waiting
  // out a fixed 15 s is what made the button feel dead. A WebView with no Web
  // Audio still records (the cap ends that take), so it never blocks the turn.
  const startedAt = Date.now();
  let heardSpeech = false;
  let lastLoudAt = 0;
  let meterRunning = false;
  const stopTake = () => {
    if (active.state === 'recording') active.stop();
  };
  try {
    const Ctx = window.AudioContext
      || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctx && stream) {
      const context = new Ctx();
      meterCtx = context;
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      context.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      meterRunning = true;
      meter = window.setInterval(() => {
        analyser.getByteFrequencyData(data);
        const level = Math.min(100, Math.round((data.reduce((sum, v) => sum + v, 0) / data.length) * 2.2));
        emit({ type: 'level', level });
        if (level >= 7) {
          heardSpeech = true;
          lastLoudAt = Date.now();
        }
      }, 100);
    }
  } catch {
    // No meter, but the turn still runs.
  }
  if (meterRunning) {
    silenceWatch = window.setInterval(() => {
      const now = Date.now();
      // Spoken, then quiet: that was the question.
      if (heardSpeech && now - lastLoudAt >= 1100) stopTake();
      // Nothing said at all: end early with "I did not hear anything" rather
      // than recording five seconds of a silent room.
      else if (!heardSpeech && now - startedAt >= 5000) stopTake();
    }, 150);
  }
  // The hard cap: no recording is left running if the person walks away. It is
  // bound to this take's recorder, so it cannot end a later one mid-sentence.
  hardCap = window.setTimeout(stopTake, 15000);
}

const web: JarvisAssistPlugin = {
  async start() {
    handsFree = true;
    await webStart();
  },
  async stop() {
    handsFree = false;
    // Cancels a start whose permission prompt is still open, so closing the
    // sheet cannot leave a recording running behind it.
    startSeq += 1;
    turnAbort?.abort();
    turnAbort = null;
    if (recorder && recorder.state === 'recording') {
      discard = true;
      recorder.stop();
    }
    stopSpeech();
    releaseMic();
    setState('idle');
  },
  async stopSpeaking() {
    stopSpeech();
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
  // Both halves are stopped, always. The sheet's close button has to end the
  // native service too, or a hands-free turn keeps the microphone open behind it.
  stop: async () => {
    await web.stop();
    if (Capacitor.isNativePlatform()) {
      try {
        await native.stop();
      } catch {
        // An older APK has no native service to stop.
      }
    }
  },
  stopSpeaking: async () => {
    await web.stopSpeaking();
    if (Capacitor.isNativePlatform()) {
      try {
        await native.stopSpeaking();
      } catch {
        // An older APK has no native service to silence.
      }
    }
  },
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
  /**
   * The web turn is what runs on every device - including inside the phone app,
   * where `start()` deliberately uses it so a tap can never start a microphone
   * service that Android refuses. So the web listeners must always be attached;
   * on a phone the native plugin's events are attached as well (a hands-free
   * native turn speaks through them), and both are removed together.
   */
  addListener: (eventName, listenerFunc) => {
    const registered: Array<Promise<{ remove: () => Promise<void> } | null>> = [
      web.addListener(eventName, listenerFunc).catch(() => null),
    ];
    if (Capacitor.isNativePlatform()) {
      registered.push(
        Promise.resolve()
          .then(() => native.addListener(eventName, listenerFunc))
          .catch(() => null),
      );
    }
    return Promise.all(registered).then((handles) => ({
      remove: async () => {
        for (const handle of handles) {
          if (!handle) continue;
          try {
            await handle.remove();
          } catch {
            // already gone with the page it belonged to
          }
        }
      },
    }));
  },
};
