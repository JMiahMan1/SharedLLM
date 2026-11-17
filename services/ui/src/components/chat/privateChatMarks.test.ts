import { describe, expect, it } from 'vitest';
import {
  REACTIONS,
  myReactionIds,
  openMarks,
  pendingAnimations,
  reactionChips,
  readMarkRecord,
  sameReactionIds,
  type MessageMark,
  type OpenedMark,
  type OpenedReaction,
} from './privateChatModel';

const HEART = REACTIONS[0].emoji;
const LIKE = REACTIONS[1].emoji;

// A stand-in for the vault: "sealed" values are JSON here, and "bad" never opens.
const open = async (sealed: string): Promise<unknown> => {
  if (sealed === 'bad') throw new Error('wrong key');
  return JSON.parse(sealed) as unknown;
};
const seal = (value: object) => JSON.stringify(value);

const reaction = (id: string, by: string, e: string, extra: Partial<MessageMark['reactions'][number]> = {}, m = 7) => ({
  id, by, by_name: by === 'sam' ? 'Sam' : 'Alex', own: by === 'alex', at: 100, seen: by === 'alex', sealed: seal({ t: 'reaction', m, e }), ...extra,
});

describe('readMarkRecord', () => {
  it('keeps a record only for the message it was sealed for', () => {
    expect(readMarkRecord({ t: 'edit', m: 7, text: 'fixed' }, 7, 'edit')).toEqual({ t: 'edit', m: 7, text: 'fixed' });
    expect(readMarkRecord({ t: 'edit', m: 8, text: 'moved' }, 7, 'edit')).toBeNull();
    expect(readMarkRecord({ t: 'reaction', m: 7, e: HEART }, 7, 'edit')).toBeNull();
  });

  it('refuses an empty edit and an emoji this version has no animation for', () => {
    expect(readMarkRecord({ t: 'edit', m: 7, text: '   ' }, 7, 'edit')).toBeNull();
    expect(readMarkRecord({ t: 'reaction', m: 7, e: 'x' }, 7, 'reaction')).toBeNull();
    expect(readMarkRecord(null, 7, 'reaction')).toBeNull();
  });
});

describe('openMarks', () => {
  it('merges the latest text and readable reactions per message', async () => {
    const marks: MessageMark[] = [
      { message_id: 7, edit: { at: 50, by: 'alex', own: true, sealed: seal({ t: 'edit', m: 7, text: 'Dinner at eight' }) },
        reactions: [reaction('b', 'sam', HEART, { at: 200 }), reaction('a', 'alex', LIKE, { at: 150 })] },
      { message_id: 9, edit: null, reactions: [reaction('c', 'sam', HEART, { sealed: 'bad' }, 9)] },
      // Sealed for message 7, listed under 8: the server moved it, so it is not shown.
      { message_id: 8, edit: { at: 1, by: 'alex', own: true, sealed: seal({ t: 'edit', m: 7, text: 'moved' }) }, reactions: [] },
    ];
    const opened = await openMarks(marks, open);
    expect(Object.keys(opened)).toEqual(['7']);
    expect(opened[7].edit).toEqual({ text: 'Dinner at eight', at: 50 });
    expect(opened[7].reactions.map((r) => r.id)).toEqual(['a', 'b']);
    expect(opened[7].reactions[1]).toMatchObject({ emoji: HEART, by: 'sam', byName: 'Sam', own: false, seen: false });
  });
});

const opened = (id: string, by: string, emoji: string, extra: Partial<OpenedReaction> = {}): OpenedReaction => ({
  id, messageId: 7, by, byName: by === 'sam' ? 'Sam' : 'Alex', own: by === 'alex', at: 100, seen: by === 'alex', emoji, ...extra,
});

describe('reactionChips', () => {
  it('counts each person once per emoji, in the order the emoji arrived, and says whose', () => {
    const chips = reactionChips([
      opened('1', 'sam', LIKE, { at: 1 }),
      opened('2', 'alex', HEART, { at: 2 }),
      opened('3', 'sam', HEART, { at: 3 }),
      opened('4', 'alex', HEART, { at: 4 }), // a double tap that raced itself
    ]);
    expect(chips).toEqual([
      { emoji: LIKE, count: 1, names: ['Sam'], mine: false },
      { emoji: HEART, count: 2, names: ['You', 'Sam'], mine: true },
    ]);
  });
});

describe('myReactionIds', () => {
  it('finds every copy of mine with that emoji, never the other parent\'s', () => {
    const list = [opened('1', 'alex', HEART), opened('2', 'alex', HEART), opened('3', 'sam', HEART), opened('4', 'alex', LIKE)];
    expect(myReactionIds(list, HEART)).toEqual(['1', '2']);
    expect(myReactionIds(undefined, HEART)).toEqual([]);
  });
});

describe('pendingAnimations', () => {
  const marks: Record<number, OpenedMark> = {
    7: { reactions: [opened('a', 'sam', HEART, { at: 30 }), opened('b', 'alex', LIKE, { at: 10 }), opened('c', 'sam', HEART, { at: 40 })] },
    9: { reactions: [{ ...opened('d', 'sam', LIKE, { at: 20 }), messageId: 9 }, { ...opened('e', 'sam', HEART, { at: 50, seen: true }), messageId: 9 }] },
  };

  it('plays the other parent\'s unseen reactions oldest first, never mine or seen ones, and each tap of one emoji once', () => {
    expect(pendingAnimations(marks, new Set())).toEqual([
      { id: 'd', messageId: 9, emoji: LIKE },
      { id: 'a', messageId: 7, emoji: HEART },
    ]);
  });

  it('skips what already played here while the server catches up', () => {
    expect(pendingAnimations(marks, new Set(['d'])).map((p) => p.id)).toEqual(['a']);
  });

  it('marks the repeated taps seen with the one that played', () => {
    expect(sameReactionIds(marks, { id: 'a', messageId: 7, emoji: HEART })).toEqual(['a', 'c']);
  });
});
