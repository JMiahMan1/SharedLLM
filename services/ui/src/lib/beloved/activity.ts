/**
 * Beloved's metadata-only signals: what was read, by whom and when.
 *
 * The gateway's `/beloved/activity` answer holds only `{who, who_name, what, at}`:
 * never a note, a prayer, a date idea or any other sealed content. Everything
 * here works on that metadata alone, so the "Today with" strip and the little
 * "Read" markers cannot leak content even by accident.
 */
import { formatDay, localToday, type Ymd } from './dates';

export type BelovedSeenWhat = 'gallery' | 'note' | 'prayer' | 'idea' | 'page';

export interface BelovedActivityEntry {
  who: string;
  who_name?: string;
  what: string;
  /** Server seconds. */
  at: number;
}

export interface BelovedPrefs {
  share_activity: boolean;
  encouragements: boolean;
  /** 7 to 21, the local hour an encouragement may arrive. */
  encourage_hour: number;
}

export const DEFAULT_BELOVED_PREFS: BelovedPrefs = { share_activity: true, encouragements: true, encourage_hour: 19 };

const PHRASES: Readonly<Record<BelovedSeenWhat, string>> = {
  gallery: 'is looking through your photos',
  note: 'read your love note',
  prayer: 'read your prayer request',
  idea: 'saw your date night idea',
  page: 'opened Beloved',
};

/** A short, content-free phrase for one reading signal, or nothing for an unknown one. */
export function activityPhrase(what: string): string | undefined {
  return PHRASES[what as BelovedSeenWhat];
}

/** In local calendar days, so a signal from yesterday evening is not "today". */
function isToday(at: number, today: Ymd): boolean {
  if (!Number.isFinite(at) || at <= 0) return false;
  return formatDay(localToday(new Date(at * 1000))) === formatDay(today);
}

/**
 * The spouse's signals from today, newest first, as short phrases. The strip
 * stays a strip: at most `limit` phrases. Unknown kinds are dropped rather than
 * shown verbatim, so nothing surprising from a newer server reaches the page.
 */
export function todayPhrases(
  activity: readonly BelovedActivityEntry[],
  me: string,
  spouse: string,
  now: Date = new Date(),
  limit = 3,
): string[] {
  if (!spouse || spouse === me || limit <= 0) return [];
  const today = localToday(now);
  return activity
    .filter((e) => e.who === spouse && e.who !== me && isToday(e.at, today) && activityPhrase(e.what))
    .slice()
    .sort((a, b) => b.at - a.at)
    .slice(0, limit)
    .map((e) => activityPhrase(e.what) as string);
}

/** True when the spouse's activity has a read of this kind after the record was written. */
export function hasRead(
  activity: readonly BelovedActivityEntry[],
  kind: 'note' | 'prayer' | 'idea',
  recordAt: number,
  spouse: string,
): boolean {
  if (!spouse || !Number.isFinite(recordAt)) return false;
  return activity.some((e) =>
    e.who === spouse && e.what === kind && Number.isFinite(e.at) && e.at > recordAt);
}
