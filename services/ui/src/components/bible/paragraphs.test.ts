import { describe, expect, it } from 'vitest';
import { paragraphs } from './paragraphs';
import type { BibleVerse } from '../../types/api';

const v = (verse: number, text: string, chapter = 3): BibleVerse => ({
  version: 'NKJV', osis: 'John', book_name: 'John', chapter, verse, reference: `John ${chapter}:${verse}`, text,
});

describe('paragraphs', () => {
  it('closes a paragraph at a sentence end once it holds a few verses', () => {
    const groups = paragraphs([v(1, 'One,'), v(2, 'two.'), v(3, 'three.'), v(4, 'four”'), v(5, 'five.”'), v(6, 'six')]);
    expect(groups.map((g) => g.map((x) => x.verse))).toEqual([[1, 2, 3], [4, 5, 6]]);
  });

  it('never runs past eight verses or across a chapter', () => {
    const long = Array.from({ length: 10 }, (_, i) => v(i + 1, 'and then,'));
    expect(paragraphs(long).map((g) => g.length)).toEqual([8, 2]);
    expect(paragraphs([v(36, 'end', 3), v(1, 'start', 4)]).length).toBe(2);
  });
});
