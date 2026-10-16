import type { BibleMark } from '../../types/api';

/** The part of a verse a mark is matched on. */
interface VerseAddress {
  osis: string;
  chapter: number;
  verse: number;
}

/**
 * Every mark that covers one verse.
 *
 * The book is part of the match: marks are fetched for the whole Bible, so
 * matching on chapter and verse alone would show a Genesis 3:16 highlight on
 * John 3:16 (and let the reader delete it from there).
 */
export function marksOnVerse(marks: BibleMark[], verse: VerseAddress): BibleMark[] {
  return marks.filter(
    (m) =>
      m.osis === verse.osis &&
      m.chapter === verse.chapter &&
      m.verse_start <= verse.verse &&
      (m.verse_end ?? m.verse_start) >= verse.verse,
  );
}

/** The highlight on a verse, if it has one. Bookmarks and notes are not washes. */
export function highlightOn(marks: BibleMark[], verse: VerseAddress): BibleMark | undefined {
  return marksOnVerse(marks, verse).find((m) => m.kind === 'highlight');
}

