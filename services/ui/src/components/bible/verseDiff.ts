/**
 * Which verses two translations word materially differently.
 *
 * Every translation differs from every other in small ways ("heaven" and
 * "heavens", a comma, a capital), so a character diff would mark nearly every
 * verse and tell the reader nothing. Instead both sides are normalised (case,
 * accents, punctuation and whitespace dropped) and compared as bags of words:
 * the share of words they have in common (the Dice coefficient over word
 * counts). A verse is marked only when that share falls below
 * `DIFF_THRESHOLD`.
 *
 * A verse one translation leaves out is never called a difference: there is
 * nothing to compare, so it is reported as missing from that side and the
 * reader is told "not in this translation" (NIV2011 has no Matthew 17:21).
 */
import type { BibleVerse } from '../../types/api';

/**
 * Under this share of shared words a verse counts as worded differently.
 * At 0.6, "the heaven and the earth" against "the heavens and the earth" (0.9)
 * or John 3:16 in the KJV against the WEB (about 0.75) are the same verse;
 * a paraphrase that keeps barely half the words is not.
 */
export const DIFF_THRESHOLD = 0.6;

export type VerseDiffStatus = 'same' | 'differs' | 'only-primary' | 'only-compare';

export interface VerseDiff {
  status: VerseDiffStatus;
  /** 0..1 share of shared words; `null` when one side is missing. */
  similarity: number | null;
}

/** `"chapter:verse"`, the same key the panes put in `data-verse-key`. */
export function verseKey(verse: { chapter: number; verse: number }): string {
  return `${verse.chapter}:${verse.verse}`;
}

/** Lower-case words with accents, punctuation and extra whitespace removed. */
export function normaliseWords(text: string): string[] {
  return text
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/['’‘]/g, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .split(' ')
    .filter(Boolean);
}

/** Share of words two texts have in common, 0..1 (Dice coefficient over word counts). */
export function wordSimilarity(a: string, b: string): number {
  const left = normaliseWords(a);
  const right = normaliseWords(b);
  if (left.length === 0 && right.length === 0) return 1;
  if (left.length === 0 || right.length === 0) return 0;
  const counts = new Map<string, number>();
  for (const word of left) counts.set(word, (counts.get(word) ?? 0) + 1);
  let shared = 0;
  for (const word of right) {
    const remaining = counts.get(word) ?? 0;
    if (remaining > 0) {
      shared += 1;
      counts.set(word, remaining - 1);
    }
  }
  return (2 * shared) / (left.length + right.length);
}

/**
 * Every verse in either translation, keyed `"chapter:verse"`, with whether it
 * differs. A verse with no words on one side counts as missing from that side.
 */
export function diffVerses(
  primary: BibleVerse[],
  compare: BibleVerse[],
  threshold: number = DIFF_THRESHOLD,
): Map<string, VerseDiff> {
  const present = (verses: BibleVerse[]) => {
    const out = new Map<string, string>();
    for (const verse of verses) {
      if (normaliseWords(verse.text).length > 0) out.set(verseKey(verse), verse.text);
    }
    return out;
  };
  const left = present(primary);
  const right = present(compare);
  const result = new Map<string, VerseDiff>();
  for (const [key, text] of left) {
    const other = right.get(key);
    if (other === undefined) {
      result.set(key, { status: 'only-primary', similarity: null });
      continue;
    }
    const similarity = wordSimilarity(text, other);
    result.set(key, { status: similarity < threshold ? 'differs' : 'same', similarity });
  }
  for (const key of right.keys()) {
    if (!left.has(key)) result.set(key, { status: 'only-compare', similarity: null });
  }
  return result;
}

/** The keys of the verses worded differently, for marking either pane. */
export function differingKeys(diff: Map<string, VerseDiff>): Set<string> {
  return new Set([...diff].filter(([, entry]) => entry.status === 'differs').map(([key]) => key));
}
