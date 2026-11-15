import { describe, expect, it } from 'vitest';
import { previewLine, type TalkMessage } from './chatModel';
import { canNudge, errorText, mayUsePrivateChat, passwordProblem } from './privateChatModel';

const me = new Set(['alex']);
const msg = (id: number, actor: string, extra: Partial<TalkMessage> = {}): TalkMessage => ({ id, actor_id: actor, message: 'x', ...extra });

describe('canNudge', () => {
  it('only while my latest message is unread by the other parent', () => {
    expect(canNudge([msg(1, 'sam'), msg(2, 'alex')], me, 1)).toBe(true);
    expect(canNudge([msg(1, 'sam'), msg(2, 'alex')], me, 2)).toBe(false);
    expect(canNudge([msg(1, 'alex'), msg(2, 'sam')], me, 0)).toBe(false);
    expect(canNudge([], me, 0)).toBe(false);
  });

  it('ignores deleted messages, system lines and messages still sending', () => {
    expect(canNudge([msg(1, 'alex'), msg(2, 'sam', { deleted: true })], me, 0)).toBe(true);
    expect(canNudge([msg(1, 'alex'), msg(2, 'sam', { system_message: 'joined' })], me, 0)).toBe(true);
    expect(canNudge([msg(1, 'sam'), { id: 'local-1', pending: 'sending', message: 'x' }], me, 0)).toBe(false);
  });
});

describe('passwordProblem', () => {
  it('asks for a long enough password typed the same twice', () => {
    expect(passwordProblem('short', 'short')).toMatch(/at least/);
    expect(passwordProblem('long enough', 'long enougH')).toMatch(/do not match/);
    expect(passwordProblem('long enough', 'long enough')).toBeNull();
  });
});

describe('errorText', () => {
  it('prefers the server\'s reason', () => {
    expect(errorText({ response: { data: { detail: 'You can nudge again in 4 min.' } } }, 'x')).toBe('You can nudge again in 4 min.');
    expect(errorText(new Error('boom'), 'x')).toBe('boom');
    expect(errorText(null, 'fallback')).toBe('fallback');
  });
});

describe('previewLine', () => {
  it('never previews a private chat, whatever the server sent', () => {
    expect(previewLine({ token: 't', display_name: 'Private chat', private: true, last_message: 'leaked text' }, me)).toBe('Encrypted message');
  });
});

describe('who may see the private chat', () => {
  it('is only an adult father or mother', () => {
    expect(mayUsePrivateChat({ family_role: 'father', is_minor: false })).toBe(true);
    expect(mayUsePrivateChat({ family_role: 'mother' })).toBe(true);
    for (const family_role of ['child', 'teen', 'adult_child', 'other', null, undefined]) {
      expect(mayUsePrivateChat({ family_role, is_minor: false })).toBe(false);
    }
    expect(mayUsePrivateChat({ family_role: 'mother', is_minor: true })).toBe(false);
    expect(mayUsePrivateChat({ family_role: 'father', is_system_default: true })).toBe(false);
    expect(mayUsePrivateChat(undefined)).toBe(false);
    expect(mayUsePrivateChat(null)).toBe(false);
  });
});
