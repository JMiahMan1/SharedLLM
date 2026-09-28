import { describe, it, expect } from 'vitest';
import { decodeEnvelope, encodeEnvelope, activityEnvelope } from './chatEnvelope';

describe('chat envelope', () => {
  it('leaves plain text alone', () => {
    expect(decodeEnvelope('dinner is in the oven')).toEqual({ text: 'dinner is in the oven', envelope: null });
  });

  it('round-trips an activity card with stats and stars', () => {
    const card = activityEnvelope({
      title: 'First 10k steps',
      detail: 'Mom hit her goal',
      stars: 2,
      stats: [{ label: 'Steps', value: '10,240' }],
    });

    const { text, envelope } = decodeEnvelope(encodeEnvelope('Nice work!', card));

    expect(text).toBe('Nice work!');
    expect(envelope).toEqual(card);
  });

  it('keeps old clients working: the text is always readable', () => {
    const wire = encodeEnvelope('Someone earned a badge', { kind: 'game', title: 'Quiz winner', stars: 1 });
    expect(wire.startsWith('Someone earned a badge')).toBe(true);
  });

  it('degrades to text when the card is malformed', () => {
    const { text, envelope } = decodeEnvelope('hello\n\n```jarvis-envelope\n{not json}\n```jarvis-envelope');
    expect(envelope).toBeNull();
    expect(text).toBe('hello');
  });

  it('rejects a card with an unknown kind or no title', () => {
    expect(decodeEnvelope('x\n\n```jarvis-envelope\n{"kind":"nope","title":"x"}\n```jarvis-envelope').envelope).toBeNull();
    expect(decodeEnvelope('x\n\n```jarvis-envelope\n{"kind":"game","title":"   "}\n```jarvis-envelope').envelope).toBeNull();
  });

  it('drops malformed stats instead of failing the message', () => {
    const { envelope } = decodeEnvelope(
      'x\n\n```jarvis-envelope\n{"kind":"activity","title":"t","stats":[{"label":1}]}\n```jarvis-envelope',
    );
    expect(envelope).toEqual({ kind: 'activity', title: 't' });
  });

  it('handles a text envelope as no card at all', () => {
    expect(encodeEnvelope('just words', { kind: 'text', title: 'ignored' })).toBe('just words');
  });
});
