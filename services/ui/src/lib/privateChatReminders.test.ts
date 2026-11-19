import { describe, expect, it } from 'vitest';
import { asksSomething, LOOK_BACK, reminderText, replyFlag, type ReminderItem } from './privateChatReminders';

describe('asksSomething', () => {
  it.each([
    'Dinner at 6?',
    'are you home',
    'Are you picking up the kids today',
    'what time is dinner',
    "What's the plan for Saturday",
    'whats for dinner',
    'How about pizza tonight',
    'Why not',
    'Hey, do you want to go out Friday',
    'Okay. Can we talk tonight',
    'Is the oven off',
    'Did you call your mom',
    "Don't you think we should wait",
    'Let me know when you land',
    'lmk',
    'Thinking about the trip. Thoughts',
    'Any ideas for her birthday',
    'Could you grab milk on the way',
    'Which one did you like',
    '¿Vienes a cenar',
    'Mañana？',
  ])('a question: %s', (text) => {
    expect(asksSomething(text)).toBe(true);
  });

  it.each([
    'Love you',
    'On my way home',
    'When I got home the dog was out',
    'Do the dishes please',
    "Can't wait to see you",
    'Will do',
    'It is what it is',
    'Where we went last year was lovely',
    'How I miss you',
    '',
    null,
    undefined,
  ])('not a question: %s', (text) => {
    expect(asksSomething(text)).toBe(false);
  });
});

function item(id: number, mine: boolean, text: string | null | undefined): ReminderItem {
  return { id, mine, text };
}

describe('replyFlag', () => {
  it('flags the newest message when one of the partner messages since mine asks something', () => {
    const items = [item(1, true, 'Home at 6'), item(2, false, 'Are you bringing bread'), item(3, false, 'Also love you')];
    expect(replyFlag(items)).toEqual({ messageId: 3, expectsReply: true });
  });

  it('says no question when there is none', () => {
    expect(replyFlag([item(1, false, 'Love you'), item(2, false, 'See you soon')])).toEqual({ messageId: 2, expectsReply: false });
  });

  it('nothing to say when my message is the newest (I already answered)', () => {
    expect(replyFlag([item(1, false, 'Are you home?'), item(2, true, 'Yes')])).toBeNull();
    expect(replyFlag([])).toBeNull();
  });

  it('only looks at messages since my last one', () => {
    const items = [item(1, false, 'Are you home?'), item(2, true, 'Yes'), item(3, false, 'Great')];
    expect(replyFlag(items)).toEqual({ messageId: 3, expectsReply: false });
  });

  it(`looks at no more than the last ${LOOK_BACK}`, () => {
    const items = [item(1, false, 'Did you eat?'), ...[2, 3, 4].map((id) => item(id, false, 'ok'))];
    expect(replyFlag(items)).toEqual({ messageId: 4, expectsReply: false });
    expect(replyFlag(items, 4)).toEqual({ messageId: 4, expectsReply: true });
  });

  it('waits until the messages it looks at are decrypted', () => {
    expect(replyFlag([item(1, false, undefined), item(2, false, 'hello')])).toBeNull();
    // A picture with no caption has no words, and that is an answer.
    expect(replyFlag([item(1, false, null)])).toEqual({ messageId: 1, expectsReply: false });
  });
});

describe('reminderText', () => {
  it('reads text, captions, comments and edits, and nothing else', () => {
    expect(reminderText({ t: 'text', text: 'hi?' })).toBe('hi?');
    expect(reminderText({ t: 'image', blob: 'b', mime: 'image/jpeg', caption: 'this one?' })).toBe('this one?');
    expect(reminderText({ t: 'image', blob: 'b', mime: 'image/jpeg' })).toBeNull();
    expect(reminderText({ t: 'comment', blob: 'b', text: 'where was this' })).toBe('where was this');
    expect(reminderText({ t: 'text', text: 'old' }, 'new?')).toBe('new?');
    expect(reminderText('unreadable')).toBeNull();
    expect(reminderText(undefined)).toBeUndefined();
  });
});
