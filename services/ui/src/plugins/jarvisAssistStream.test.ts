import { describe, expect, it, vi, beforeEach } from 'vitest';

/**
 * A spoken turn answers the way the Echo Dots do: each sentence is synthesised
 * as it is written, and they are played in the order the model said them.
 * These tests pin the order and the two failure paths that used to go silent.
 */
const spoken: string[] = [];
const sentences: string[] = [];

vi.mock('../services/api', () => ({
  api: {
    voiceListen: vi.fn(async () => ({ text: '' })),
    voiceSpeak: vi.fn(async (text: string) => {
      spoken.push(text);
      return { audio_b64: '', mime: 'audio/wav', voice: null };
    }),
    voiceChatStream: vi.fn(
      async (
        _question: string,
        options: {
          onSentence: (sentence: { text: string; voice: string | null }) => void;
          onReply?: (text: string) => void;
        },
      ) => {
        for (const sentence of sentences) {
          options.onSentence({ text: sentence, voice: null });
          // Let the queue start this sentence before the next one exists.
          await Promise.resolve();
        }
        options.onReply?.(sentences.join(' '));
        return sentences.join(' ');
      },
    ),
  },
}));

const { askJarvis, JarvisAssist } = await import('./jarvisAssist');

const events: Array<{ type: string; text?: string }> = [];

beforeEach(() => {
  vi.clearAllMocks();
  spoken.length = 0;
  sentences.length = 0;
  events.length = 0;
});

describe('a spoken answer', () => {
  it('speaks every sentence, in the order the model wrote them', async () => {
    sentences.push('The office is twenty one degrees.', 'The meeting starts at three.');
    await JarvisAssist.addListener('assist', (event) => events.push(event));
    await askJarvis('hey jarvis, how is the office?');
    expect(spoken).toEqual(['The office is twenty one degrees.', 'The meeting starts at three.']);
    expect(events.some((event) => event.type === 'sentence')).toBe(true);
    expect(events.some((event) => event.type === 'reply')).toBe(true);
    expect(events.at(-1)).toEqual({ type: 'state', text: 'idle' });
  });

  it('drops the wake phrase from the question it sends', async () => {
    sentences.push('It is noon.');
    const { api } = await import('../services/api');
    await askJarvis('Hey Jarvis, what time is it?');
    expect(api.voiceChatStream).toHaveBeenCalledWith(
      'what time is it?',
      expect.objectContaining({ deviceName: expect.any(String) }),
    );
  });

  it('an empty question never reaches the house', async () => {
    await askJarvis('   ');
    const { api } = await import('../services/api');
    expect(api.voiceChatStream).not.toHaveBeenCalled();
  });
});
