import { describe, it, expect, beforeEach, vi } from 'vitest';
import { clearNoteDraft, readNoteDraft, readNoteDrafts, writeNoteDraft } from '../lib/noteDrafts';

const draft = (title: string) => ({ title, content: 'x', folder: '', color: 'default' as const, pinned: false });

describe('noteDrafts', () => {
  beforeEach(() => localStorage.clear());

  it('keeps drafts per account and forgets one on request', () => {
    writeNoteDraft('me', 'Notes/A.md', { ...draft('A'), path: 'Notes/A.md', etag: '"v3"' });
    writeNoteDraft('admin', 'new:1', draft('B'));

    expect(readNoteDraft('me', 'Notes/A.md')).toMatchObject({ title: 'A', etag: '"v3"' });
    expect(readNoteDrafts('admin')).toHaveProperty('new:1');
    expect(readNoteDrafts('me')).not.toHaveProperty('new:1');

    clearNoteDraft('me', 'Notes/A.md');
    expect(readNoteDrafts('me')).toEqual({});
  });

  it('keeps only the newest twenty', () => {
    const now = vi.spyOn(Date, 'now');
    for (let n = 0; n < 25; n += 1) {
      now.mockReturnValue(1_000 + n);
      writeNoteDraft('me', `new:${n}`, draft(`D${n}`));
    }
    now.mockRestore();
    const ids = Object.keys(readNoteDrafts('me'));
    expect(ids).toHaveLength(20);
    expect(ids).not.toContain('new:0');
    expect(ids).toContain('new:24');
  });

  it('ignores a damaged store and survives storage that throws', () => {
    localStorage.setItem('jarvis_notes_drafts_v1:me', '{not json');
    expect(readNoteDrafts('me')).toEqual({});
    localStorage.setItem('jarvis_notes_drafts_v1:me', JSON.stringify({ a: { title: 1 } }));
    expect(readNoteDrafts('me')).toEqual({});

    const set = vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    expect(() => writeNoteDraft('me', 'new:1', draft('A'))).not.toThrow();
    set.mockRestore();
  });
});
