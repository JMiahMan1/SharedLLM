import { describe, expect, it } from 'vitest';
import { highlightOn, marksOnVerse } from './verseMarks';
import type { BibleMark } from '../../types/api';

const mark = (patch: Partial<BibleMark>): BibleMark => ({
  id: 1, version_code: 'kjv', osis: 'John', book_name: 'John', chapter: 3, verse_start: 16, verse_end: 16,
  kind: 'highlight', color: 'yellow', note_path: '', note_preview: '', created_at: null, updated_at: null, ...patch,
});

describe('marks on a verse', () => {
  it('matches the book as well as the chapter and verse', () => {
    const marks = [mark({ id: 1, osis: 'Gen', book_name: 'Genesis' }), mark({ id: 2, kind: 'bookmark' })];
    expect(marksOnVerse(marks, { osis: 'John', chapter: 3, verse: 16 }).map((m) => m.id)).toEqual([2]);
    expect(highlightOn(marks, { osis: 'John', chapter: 3, verse: 16 })).toBeUndefined();
    expect(highlightOn(marks, { osis: 'Gen', chapter: 3, verse: 16 })?.id).toBe(1);
  });

  it('covers every verse of a range', () => {
    const marks = [mark({ verse_start: 14, verse_end: 17 })];
    expect(marksOnVerse(marks, { osis: 'John', chapter: 3, verse: 17 })).toHaveLength(1);
    expect(marksOnVerse(marks, { osis: 'John', chapter: 3, verse: 18 })).toHaveLength(0);
  });
});
