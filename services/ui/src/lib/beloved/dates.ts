/**
 * Beloved's calendar arithmetic: plain calendar days, never instants.
 *
 * A wedding date or a birthday is a day on the calendar, not a moment, so
 * every date here is `YYYY-MM-DD` and every count is made on day numbers
 * (days since 1970-01-01 by the proleptic Gregorian calendar, via Date.UTC).
 * "Today" is the phone's own local date. That keeps "married 1,000 days" the
 * same in every time zone and across daylight saving changes, where counting
 * milliseconds and dividing by 86,400,000 is a day off twice a year.
 */

export interface Ymd {
  y: number;
  m: number;
  d: number;
}

const MS_PER_DAY = 86_400_000;

/** A `YYYY-MM-DD` string as a real calendar day, or null. */
export function parseDay(text: unknown): Ymd | null {
  if (typeof text !== 'string') return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) return null;
  const [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])];
  if (m < 1 || m > 12 || d < 1 || d > daysInMonth(y, m)) return null;
  return { y, m, d };
}

export function formatDay({ y, m, d }: Ymd): string {
  return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

export function isLeapYear(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

export function daysInMonth(y: number, m: number): number {
  return [31, isLeapYear(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];
}

/** Days since 1970-01-01 (negative before it). */
export function dayNumber({ y, m, d }: Ymd): number {
  return Math.round(Date.UTC(y, m - 1, d) / MS_PER_DAY);
}

export function fromDayNumber(n: number): Ymd {
  const date = new Date(n * MS_PER_DAY);
  return { y: date.getUTCFullYear(), m: date.getUTCMonth() + 1, d: date.getUTCDate() };
}

/** The phone's local calendar day at `now`. */
export function localToday(now: Date = new Date()): Ymd {
  return { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };
}

/**
 * A yearly day (anniversary, birthday) in a given year. Someone born or
 * married on 29 February keeps it on 28 February in the other three years.
 */
export function occurrenceIn(day: Ymd, year: number): Ymd {
  return { y: year, m: day.m, d: Math.min(day.d, daysInMonth(year, day.m)) };
}

export interface MarriedFor {
  /** Whole days since the wedding (the wedding day is day 0). */
  days: number;
  /** Whole years, then months, then days, as a calendar reads them. */
  years: number;
  months: number;
  remainderDays: number;
}

/** How long since `start`, or null when it is still to come. */
export function elapsed(start: Ymd, today: Ymd): MarriedFor | null {
  const days = dayNumber(today) - dayNumber(start);
  if (days < 0) return null;
  // Whole months first; the anniversary of the start in the last month (clamped
  // to that month's length) is where the leftover days are counted from.
  let total = (today.y - start.y) * 12 + (today.m - start.m);
  if (today.d < start.d) total -= 1;
  const anchorY = start.y + Math.floor((start.m - 1 + total) / 12);
  const anchorM = ((start.m - 1 + total) % 12) + 1;
  const anchor = { y: anchorY, m: anchorM, d: Math.min(start.d, daysInMonth(anchorY, anchorM)) };
  const remainderDays = dayNumber(today) - dayNumber(anchor);
  const years = Math.floor(total / 12);
  const months = total % 12;
  return { days, years, months, remainderDays };
}

export interface Upcoming {
  /** The next time it falls (today counts). */
  date: Ymd;
  /** Days from today (0 is today). */
  inDays: number;
  /** For a yearly day with a starting year: which one it will be (the 10th anniversary). */
  nth?: number;
}

/** The next occurrence of a yearly day on or after today. */
export function nextYearly(day: Ymd, today: Ymd): Upcoming {
  let date = occurrenceIn(day, today.y);
  if (dayNumber(date) < dayNumber(today)) date = occurrenceIn(day, today.y + 1);
  return { date, inDays: dayNumber(date) - dayNumber(today), nth: date.y - day.y };
}

/** A one-off day's countdown, or null when it is past. */
export function nextOnce(day: Ymd, today: Ymd): Upcoming | null {
  const inDays = dayNumber(day) - dayNumber(today);
  return inDays < 0 ? null : { date: day, inDays };
}

export interface Milestone {
  /** e.g. `days:1000` or `years:10`, stable for reminders and keys. */
  key: string;
  label: string;
  date: Ymd;
  inDays: number;
  kind: 'days' | 'years';
  value: number;
}

/**
 * The day counts worth celebrating: every 100 days to 1,000, every 500 to
 * 10,000, then every 1,000 (and 11,111, 22,222 and so on for fun).
 */
export function isDayMilestone(n: number): boolean {
  if (n <= 0) return false;
  if (n <= 1000) return n % 100 === 0;
  if (n <= 10_000) return n % 500 === 0;
  return n % 1000 === 0 || (n % 11_111 === 0 && n <= 99_999);
}

export function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`;
}

export function grouped(n: number): string {
  return n.toLocaleString('en-US');
}

/**
 * The next `count` milestones of a marriage on or after today: day counts and
 * yearly anniversaries, soonest first. Empty before the wedding.
 */
export function upcomingMilestones(wedding: Ymd, today: Ymd, count = 6, horizonDays = 366 * 60): Milestone[] {
  const start = dayNumber(wedding);
  const now = dayNumber(today);
  if (now < start) return [];
  const out: Milestone[] = [];
  for (let n = Math.max(1, now - start); n <= now - start + horizonDays && out.length < count * 2; n += 1) {
    if (isDayMilestone(n)) {
      out.push({ key: `days:${n}`, label: `${grouped(n)} days married`, date: fromDayNumber(start + n), inDays: start + n - now, kind: 'days', value: n });
    }
  }
  for (let year = Math.max(1, today.y - wedding.y); out.length < count * 4 && year <= today.y - wedding.y + 60; year += 1) {
    const date = occurrenceIn(wedding, wedding.y + year);
    const inDays = dayNumber(date) - now;
    if (inDays < 0) continue;
    out.push({ key: `years:${year}`, label: `${ordinal(year)} anniversary`, date, inDays, kind: 'years', value: year });
    if (out.filter((m) => m.kind === 'years').length >= count) break;
  }
  return out.sort((a, b) => a.inDays - b.inDays || (a.kind === 'years' ? -1 : 1)).slice(0, count);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const LONG_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

/** "Mar 9, 2027" (or "Mar 9" without the year). */
export function dayLabel(day: Ymd, withYear = true): string {
  return withYear ? `${MONTHS[day.m - 1]} ${day.d}, ${day.y}` : `${MONTHS[day.m - 1]} ${day.d}`;
}

export function longDayLabel(day: Ymd): string {
  return `${LONG_MONTHS[day.m - 1]} ${day.d}, ${day.y}`;
}

/** "Today", "Tomorrow", "In 12 days". */
export function countdownLabel(inDays: number): string {
  if (inDays === 0) return 'Today';
  if (inDays === 1) return 'Tomorrow';
  return `In ${grouped(inDays)} days`;
}

/** "3 years, 2 months" (the largest two parts that are not zero). */
export function durationLabel(span: MarriedFor): string {
  const parts: string[] = [];
  if (span.years) parts.push(`${span.years} ${span.years === 1 ? 'year' : 'years'}`);
  if (span.months) parts.push(`${span.months} ${span.months === 1 ? 'month' : 'months'}`);
  if (span.remainderDays && parts.length < 2) parts.push(`${span.remainderDays} ${span.remainderDays === 1 ? 'day' : 'days'}`);
  return parts.slice(0, 2).join(', ') || 'Today';
}
