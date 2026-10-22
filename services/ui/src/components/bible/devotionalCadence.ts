import type { BibleImportedBookDetail } from '../../types/api';

type ChapterEntry = BibleImportedBookDetail['chapters'][number];

/** Day of the year in local time, matching the server's devotional rotation. */
export function dayOfYear(date = new Date()): number {
  const start = new Date(date.getFullYear(), 0, 1);
  return Math.floor((date.getTime() - start.getTime()) / 86_400_000) + 1;
}

export type Cadence = 'day' | 'week';

/**
 * The section today's devotional reading falls on, mirroring the server:
 * only printed devotions take a day (never the copyright or contents pages),
 * day one of the year is the first devotion, and a weekly book steps once a
 * week rather than once a day.
 */
export function todayOrdinal(chapters: ChapterEntry[], cadence: Cadence = 'day', day = dayOfYear()) {
  if (chapters.length === 0) return undefined;
  const numbered = chapters.filter((chapter) => chapter.number != null);
  const entries = numbered.length > 0 ? numbered : chapters;
  const index =
    cadence === 'week'
      ? Math.floor((day - 1) / 7) % entries.length
      : (day - 1) % entries.length;
  return entries[index]?.ordinal;
}
