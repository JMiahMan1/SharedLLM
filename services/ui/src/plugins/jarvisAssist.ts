import { Capacitor, registerPlugin } from '@capacitor/core';

/**
 * The phone's own voice assistant, as the native side sees it.
 *
 * A turn is heard, understood and answered entirely on the household's server:
 * the phone records one utterance, asks the gateway to hear it, streams the
 * answer sentence by sentence, and speaks each sentence in the signed-in
 * person's own voice. Nothing about it is cloud-bound.
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
  addListener(
    eventName: 'assist',
    listenerFunc: (event: AssistEvent) => void,
  ): Promise<{ remove: () => Promise<void> }>;
}

const native = registerPlugin<JarvisAssistPlugin>('JarvisAssist');

/**
 * On the web there is no microphone service, so the sheet falls back to typing.
 * Every method resolves: a missing assistant must not throw at a button press.
 */
const web: JarvisAssistPlugin = {
  async start() {},
  async stop() {},
  async stopSpeaking() {},
  async status() {
    return { running: false, state: 'idle' };
  },
  async speak() {},
  async setWakePhrase() {},
  async addListener() {
    return { remove: async () => {} };
  },
};

export const JarvisAssist: JarvisAssistPlugin = {
  start: (options) => (Capacitor.isNativePlatform() ? native.start(options) : web.start(options)),
  stop: () => (Capacitor.isNativePlatform() ? native.stop() : web.stop()),
  stopSpeaking: () => (Capacitor.isNativePlatform() ? native.stopSpeaking() : web.stopSpeaking()),
  status: () => (Capacitor.isNativePlatform() ? native.status() : web.status()),
  speak: (options) => (Capacitor.isNativePlatform() ? native.speak(options) : web.speak(options)),
  setWakePhrase: (options) => (Capacitor.isNativePlatform() ? native.setWakePhrase(options) : web.setWakePhrase(options)),
  addListener: (eventName, listenerFunc) =>
    Capacitor.isNativePlatform() ? native.addListener(eventName, listenerFunc) : web.addListener(eventName, listenerFunc),
};
