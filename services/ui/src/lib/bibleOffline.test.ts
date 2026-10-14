import { beforeEach, describe, expect, it } from 'vitest';
import { MAX_PASSAGES, isUnreachable, recallPassage, rememberPassage } from './bibleOffline';
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
