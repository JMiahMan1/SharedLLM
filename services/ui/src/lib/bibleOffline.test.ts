import { beforeEach, describe, expect, it } from 'vitest';
import { MAX_PASSAGES, isUnreachable, recallPassage, rememberPassage, withOfflineCopy } from './bibleOffline';
import type { BiblePassage } from '../types/api';

const passage = (ref: string): BiblePassage => ({ version: 'kjv', requested: ref, reference: ref, spans: [], verses: [], count: 0 });

beforeEach(() => localStorage.clear());

describe('offline chapters', () => {
  it('keeps a passage and marks it as the offline copy', () => {
    rememberPassage('John 3', 'kjv', passage('John 3'));
    expect(recallPassage('john 3', 'KJV')).toMatchObject({ reference: 'John 3', offline: true });
    expect(recallPassage('John 3', 'web')).toBeNull();
  });

  it('keeps only the most recent passages', () => {
    for (let i = 0; i <= MAX_PASSAGES; i++) rememberPassage(`Psalm ${i}`, 'kjv', passage(`Psalm ${i}`));
    expect(recallPassage('Psalm 0', 'kjv')).toBeNull();
    expect(recallPassage(`Psalm ${MAX_PASSAGES}`, 'kjv')).not.toBeNull();
  });

  it('falls back only when the server could not be reached', () => {
    expect(isUnreachable(new Error('Network Error'))).toBe(true);
    expect(isUnreachable({ response: { status: 400 } })).toBe(false);
  });
});

describe('the reader\'s place and lists, offline', () => {
  it('answers with the last copy only when the server cannot be reached', async () => {
    await withOfflineCopy('state', async () => ({ position: 'Ps 23' }));
    const unreachable = Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' });
    await expect(withOfflineCopy('state', () => Promise.reject(unreachable))).resolves.toEqual({ position: 'Ps 23' });

    const answered = Object.assign(new Error('401'), { response: { status: 401 } });
    await expect(withOfflineCopy('state', () => Promise.reject(answered))).rejects.toBe(answered);
  });

  it('fails as before when there is no copy yet', async () => {
    const unreachable = new Error('Network Error');
    await expect(withOfflineCopy('versions', () => Promise.reject(unreachable))).rejects.toBe(unreachable);
  });
});
