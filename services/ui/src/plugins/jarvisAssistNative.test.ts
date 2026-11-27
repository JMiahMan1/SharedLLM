import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The phone app runs the *web* turn - `start()` deliberately uses it so a tap can
 * never start a microphone foreground service that Android refuses. That turn
 * reports what it is doing through the web listeners, so on Android the plugin
 * has to attach the web listeners *and* the native ones.
 *
 * It used to attach only the native ones, and every event of a web turn was
 * emitted into a set nobody was listening to: the orb sat frozen, the heard
 * words never appeared and the answer was never shown, on a phone, with the
 * microphone working perfectly.
 */

const { native, nativeListeners, nativeCalls } = vi.hoisted(() => {
  const listeners = new Set<(event: { type: string; text?: string; level?: number }) => void>();
  const calls: string[] = [];
  const plugin = {
    start: vi.fn(async () => {
      calls.push('start');
    }),
    stop: vi.fn(async () => {
      calls.push('stop');
    }),
    stopSpeaking: vi.fn(async () => {
      calls.push('stopSpeaking');
    }),
    status: vi.fn(async () => ({ running: false, state: 'idle' })),
    speak: vi.fn(async () => undefined),
    setWakePhrase: vi.fn(async () => undefined),
    assistantRole: vi.fn(async () => ({ canHold: true, held: false })),
    requestAssistantRole: vi.fn(async () => undefined),
    requestMicrophone: vi.fn(async () => ({ granted: true })),
    addListener: vi.fn(
      async (_event: string, listener: (event: { type: string; text?: string; level?: number }) => void) => {
        listeners.add(listener);
        return {
          remove: async () => {
            listeners.delete(listener);
          },
        };
      },
    ),
  };
  return { native: plugin, nativeListeners: listeners, nativeCalls: calls };
});

type AssistListener = (event: { type: string; text?: string; level?: number }) => void;

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: () => true },
  registerPlugin: () => native,
}));

vi.mock('../services/api', () => ({
  api: {
    voiceChatStream: async (_question: string, options: Record<string, unknown>) => {
      (options.onSentence as (s: { text: string }) => void)?.({ text: 'Paris is the capital.' });
      (options.onReply as (s: string) => void)?.('Paris is the capital.');
    },
    voiceSpeak: async () => ({ audio_b64: '', mime: 'audio/wav' }),
    voiceListen: async () => ({ text: 'what is the capital of france' }),
  },
}));

import { askJarvis, JarvisAssist } from './jarvisAssist';

describe('the assistant on Android', () => {
  beforeEach(() => {
    nativeListeners.clear();
    nativeCalls.length = 0;
  });

  it('shows a web turn to the screen, because the web turn is what runs', async () => {
    const events: Array<{ type: string; text?: string }> = [];
    const handle = await JarvisAssist.addListener('assist', (event) => events.push(event));

    await askJarvis('what is the capital of france');

    expect(events.find((event) => event.type === 'transcript')?.text).toBe('what is the capital of france');
    expect(events.filter((event) => event.type === 'sentence').map((event) => event.text)).toEqual([
      'Paris is the capital.',
    ]);
    expect(events.find((event) => event.type === 'reply')?.text).toBe('Paris is the capital.');
    expect(events.some((event) => event.type === 'error')).toBe(false);

    await handle.remove();
  });

  it('also listens to the native half, for a hands-free turn', async () => {
    const handle = await JarvisAssist.addListener('assist', () => undefined);

    expect(native.addListener).toHaveBeenCalledWith('assist', expect.any(Function));
    await handle.remove();
    expect(nativeListeners.size).toBe(0);
  });

  it('closes the sheet by stopping both halves', async () => {
    await JarvisAssist.stop();

    expect(nativeCalls).toContain('stop');
  });

  it('silences both halves when the person says stop talking', async () => {
    await JarvisAssist.stopSpeaking();

    expect(nativeCalls).toContain('stopSpeaking');
  });

  it('removing a listener leaves nothing behind on either side', async () => {
    const event: AssistListener = () => undefined;
    const handle = await JarvisAssist.addListener('assist', event);
    expect(nativeListeners.has(event)).toBe(true);

    await handle.remove();
    await handle.remove(); // a second close must not throw

    expect(nativeListeners.has(event)).toBe(false);
  });
});
