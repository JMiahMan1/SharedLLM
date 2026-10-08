import { describe, expect, it } from 'vitest';
import {
  asksJarvis,
  dayLabel,
  isAssistant,
  receiptFor,
  splitRichText,
  groupIntoRuns,
  isEmojiOnly,
  isMine,
  previewLine,
  reactionCounts,
  sortMessages,
  type TalkMessage,
} from './chatModel';

const me = new Set(['jeremiah']);
const at = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);

describe('chat model', () => {
  it('reads history oldest-first although Talk sends it newest-first', () => {
    const sorted = sortMessages([
      { id: 3, timestamp: 30 },
      { id: 1, timestamp: 10 },
      { id: 2, timestamp: 20 },
    ]);
    expect(sorted.map((m) => m.id)).toEqual([1, 2, 3]);
  });

  it('knows your own messages by Nextcloud actor id, not display name', () => {
    expect(isMine({ actor_id: 'Jeremiah', actor_display_name: 'Dad' }, me)).toBe(true);
    expect(isMine({ actor_id: 'michele', actor_display_name: 'Jeremiah' }, me)).toBe(false);
    expect(isMine({ pending: 'sending' }, new Set())).toBe(true);
  });

  it('groups a burst from one sender and splits after a pause or a new sender', () => {
    const t = at('2026-10-07T18:00:00');
    const msgs: TalkMessage[] = [
      { id: 1, actor_id: 'michele', timestamp: t },
      { id: 2, actor_id: 'michele', timestamp: t + 30 },
      { id: 3, actor_id: 'jeremiah', timestamp: t + 60 },
      { id: 4, actor_id: 'jeremiah', timestamp: t + 60 + 6 * 60 },
    ];
    const runs = groupIntoRuns(msgs, me);
    expect(runs.map((r) => r.messages.map((m) => m.id))).toEqual([[1, 2], [3], [4]]);
    expect(runs.map((r) => r.mine)).toEqual([false, true, true]);
  });

  it('labels days the way a messenger does', () => {
    const now = new Date('2026-10-07T12:00:00');
    expect(dayLabel(at('2026-10-07T08:00:00'), now)).toBe('Today');
    expect(dayLabel(at('2026-10-06T23:00:00'), now)).toBe('Yesterday');
  });

  it('draws only short emoji-only messages large', () => {
    expect(isEmojiOnly('🎉')).toBe(true);
    expect(isEmojiOnly('👍👍👍')).toBe(true);
    expect(isEmojiOnly('👍👍👍👍')).toBe(false);
    expect(isEmojiOnly('ok 👍')).toBe(false);
  });

  it('prefixes the inbox preview with who said it', () => {
    expect(previewLine({ token: 'a', display_name: 'Family', type: 2, last_message: 'Dinner?', last_message_actor: 'Michele Summers', last_message_actor_id: 'michele' }, me)).toBe('Michele: Dinner?');
    expect(previewLine({ token: 'a', display_name: 'Family', type: 2, last_message: 'Yes', last_message_actor_id: 'jeremiah' }, me)).toBe('You: Yes');
    expect(previewLine({ token: 'b', display_name: 'Michele', type: 1, last_message: 'Hi', last_message_actor: 'Michele', last_message_actor_id: 'michele' }, me)).toBe('Hi');
  });

  it('counts reactions from every shape Talk returns', () => {
    expect(reactionCounts({ '👍': 2 })).toEqual({ '👍': 2 });
    expect(reactionCounts({ '❤️': [{ actorId: 'a' }, { actorId: 'b' }] })).toEqual({ '❤️': 2 });
    expect(reactionCounts([{ reaction: '🎉' }, { reaction: '🎉' }])).toEqual({ '🎉': 2 });
    expect(reactionCounts(undefined)).toEqual({});
  });

  it('fills placeholders: a file becomes an attachment, a mention a chip, a URL a link', () => {
    const parts = splitRichText('Hi {mention-user1}, see {file} at https://example.com/x.', {
      'mention-user1': { type: 'user', id: 'michele', name: 'Michele' },
      file: { type: 'file', id: '9', name: 'a.jpg', mimetype: 'image/jpeg' },
    });
    expect(parts.map((p) => p.kind)).toEqual(['text', 'mention', 'text', 'file', 'text', 'link', 'text']);
    expect(parts[5]).toMatchObject({ href: 'https://example.com/x' });
  });

  it('knows a Jarvis answer and a Jarvis question', () => {
    expect(isAssistant({ message: 'Hi\n\n```jarvis-envelope\n{"kind":"assistant","title":"Jarvis"}\n```jarvis-envelope' })).toBe(true);
    expect(isAssistant({ message: 'Hi' })).toBe(false);
    expect(isMine({ actor_id: 'jeremiah', message: 'A\n\n```jarvis-envelope\n{"kind":"assistant","title":"Jarvis"}\n```jarvis-envelope' }, me)).toBe(false);
    expect(asksJarvis('  @jarvis hi')).toBe(true);
    expect(asksJarvis('hi @Jarvis')).toBe(false);
  });

  it('says Sent until everyone has read it, then Read', () => {
    expect(receiptFor({ id: 10 }, 9)).toBe('sent');
    expect(receiptFor({ id: 10 }, 10)).toBe('read');
    expect(receiptFor({ id: 'local-1', pending: 'sending' }, 99)).toBe('sending');
  });

  it('previews attachments and Jarvis answers readably in the inbox', () => {
    expect(previewLine({ token: 'a', display_name: 'Fam', type: 2, last_message: '{file}', last_message_actor: 'Michele', last_message_actor_id: 'michele' }, me)).toBe('Michele: 📎 Attachment');
    expect(previewLine({ token: 'a', display_name: 'Fam', type: 2, last_message: 'Tacos\n\n```jarvis-envelope\n{"kind":"assistant","title":"Jarvis"}\n```jarvis-envelope', last_message_actor_id: 'jeremiah' }, me)).toBe('Jarvis: Tacos');
  });
});
