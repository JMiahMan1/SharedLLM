import type { BibleVerse } from '../../types/api';

/**
 * Where a paragraph ends. The installed texts carry no paragraph marks, so a
 * paragraph closes where a sentence ends once it holds a few verses, and never
 * runs past eight: close to how the printed editions break a chapter, and far
 * easier to read than one block or a list.
 */
export function paragraphs(verses: BibleVerse[]): BibleVerse[][] {
  const out: BibleVerse[][] = [];
  let current: BibleVerse[] = [];
  for (const verse of verses) {
    if (current.length && current[0].chapter !== verse.chapter) {
      out.push(current);
      current = [];
    }
    current.push(verse);
    const ends = /[.!?]["'”’)\]]*\s*$/.test(verse.text.trim());
    if ((ends && current.length >= 3) || current.length >= 8) {
      out.push(current);
      current = [];
    }
  }
  if (current.length) out.push(current);
  return out;
}
