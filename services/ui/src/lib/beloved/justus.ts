/**
 * "Just us": the couple's own private calendar inside Beloved.
 *
 * Days the two of them marked (`together` records) and evenings one of them
 * planned (`plan` records), both sealed on the phone like every Beloved
 * record. This module holds the arithmetic: how many days since the last one,
 * the counts for a month and a year, the month grid, and the reminders the
 * phone schedules for itself.
 *
 * Privacy is the point. Every reminder made here has the same generic title
 * and text whatever the phone's "Show what the day is" setting says, so a
 * lock screen, a notification shade or a child holding the phone learns
 * nothing. Nothing from here is shown on Beloved's home page or counted by
 * the private chat bar's heart.
 */
import { dayNumber, daysInMonth, formatDay, fromDayNumber, parseDay, type Ymd } from './dates';
import type { BelovedView, Opened, PlanRecord, TogetherRecord } from './records';
import { EVE_HOUR, DAY_HOUR, GENERIC_TITLE, localMoment, reminderId, type Reminder } from './reminders';

/** Every reminder from this tab says only this. */
export const PRIVATE_REMINDER = 'A little reminder for the two of you.';

/** Choices for "Remind me after" (days); 0 is off, the default. */
export const NUDGE_CHOICES: readonly number[] = [0, 3, 5, 7, 10, 14];

/** At most this many plan reminders are scheduled (the phone's alarm list is shared with special days). */
export const MAX_PLAN_REMINDERS = 8;
/** How far ahead plan reminders are scheduled. */
export const PLAN_HORIZON_DAYS = 120;

/** A few tasteful ideas for a planned evening. */
export interface EveningIdea {
  id: string;
  title: string;
}

export const EVENING_IDEAS: readonly EveningIdea[] = [
  { id: 'candles', title: 'Candlelit dinner at home' },
  { id: 'hotel', title: 'A night away at a hotel' },
  { id: 'nophones', title: 'A no-phones evening' },
  { id: 'dance', title: 'Slow dance in the kitchen' },
  { id: 'bath', title: 'A warm bath and an early evening in' },
  { id: 'letters', title: 'Write each other a letter and read it aloud' },
  { id: 'stars', title: 'Stargazing under a blanket' },
  { id: 'massage', title: 'Back rubs and quiet music' },
];

export function eveningIdea(id: string | undefined): EveningIdea | undefined {
  return id ? EVENING_IDEAS.find((i) => i.id === id) : undefined;
}

// --- Marked days ------------------------------------------------------------------

/** The distinct marked days on or before today, as day numbers, oldest first. */
export function markedDays(view: Pick<BelovedView, 'together'>, today: Ymd): number[] {
  const limit = dayNumber(today);
  const days = new Set<number>();
  for (const t of view.together) {
    const day = parseDay(t.record.day);
    if (day && dayNumber(day) <= limit) days.add(dayNumber(day));
  }
  return [...days].sort((a, b) => a - b);
}

/** Whole days since the last marked day (0 when it was today), or null when none is marked. */
export function daysSince(view: Pick<BelovedView, 'together'>, today: Ymd): number | null {
  const days = markedDays(view, today);
  return days.length ? dayNumber(today) - days[days.length - 1] : null;
}

/** "Today", "Yesterday", "5 days ago". */
export function sinceLabel(days: number): string {
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return `${days} days ago`;
}

/** Distinct marked days in a month (`m` given) or a whole year, up to today. */
export function countIn(view: Pick<BelovedView, 'together'>, today: Ymd, y: number, m?: number): number {
  return markedDays(view, today).filter((n) => {
    const day = fromDayNumber(n);
    return day.y === y && (m === undefined || day.m === m);
  }).length;
}

/** Whether today is already marked (by either of them). */
export function markedToday(view: Pick<BelovedView, 'together'>, today: Ymd): boolean {
  const key = formatDay(today);
  return view.together.some((t) => t.record.day === key);
}

// --- Plans ------------------------------------------------------------------------

/** Plans not yet marked done, soonest first (a past one stays until it is marked done or cancelled). */
export function openPlans(view: Pick<BelovedView, 'plans'>): Opened<PlanRecord>[] {
  return view.plans
    .filter((p) => !p.record.doneAt)
    .sort((a, b) => (a.record.day < b.record.day ? -1 : a.record.day > b.record.day ? 1 : (a.record.time ?? '') < (b.record.time ?? '') ? -1 : 1));
}

/** The `together` record a finished plan becomes. */
export function togetherFromPlan(plan: PlanRecord): TogetherRecord {
  return { b: 'together', day: plan.day };
}

// --- The month on screen -------------------------------------------------------------

export interface MonthCell {
  day: Ymd;
  key: string;
  marked: boolean;
  planned: boolean;
  today: boolean;
}

/** A month and its neighbour (`delta` months away). */
export function addMonths({ y, m }: { y: number; m: number }, delta: number): { y: number; m: number } {
  const index = y * 12 + (m - 1) + delta;
  return { y: Math.floor(index / 12), m: (index % 12) + 1 };
}

/**
 * The month as weeks of seven (Sunday first), padded with nulls before the
 * first and after the last day, each day saying whether it is marked or has an
 * open plan.
 */
export function monthGrid(view: Pick<BelovedView, 'together' | 'plans'>, y: number, m: number, today: Ymd): Array<Array<MonthCell | null>> {
  const marked = new Set(view.together.map((t) => t.record.day));
  const planned = new Set(openPlans(view).map((p) => p.record.day));
  const todayKey = formatDay(today);
  const lead = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  const cells: Array<MonthCell | null> = Array.from({ length: lead }, () => null);
  for (let d = 1; d <= daysInMonth(y, m); d += 1) {
    const day = { y, m, d };
    const key = formatDay(day);
    cells.push({ day, key, marked: marked.has(key), planned: planned.has(key), today: key === todayKey });
  }
  while (cells.length % 7) cells.push(null);
  const weeks: Array<Array<MonthCell | null>> = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

// --- Reminders ----------------------------------------------------------------------

const NUDGE_KEY = 'jarvis.beloved.after';

/** This phone's "Remind me after N days" (0 when off, the default). Per phone, never sent anywhere. */
export function nudgeAfter(): number {
  try {
    const value = Number(globalThis.localStorage?.getItem(NUDGE_KEY));
    return NUDGE_CHOICES.includes(value) ? value : 0;
  } catch {
    return 0;
  }
}

export function setNudgeAfter(days: number): void {
  try {
    if (days && NUDGE_CHOICES.includes(days)) globalThis.localStorage.setItem(NUDGE_KEY, String(days));
    else globalThis.localStorage.removeItem(NUDGE_KEY);
  } catch {
    // Not kept: the reminder stays off.
  }
}

function minutes(time: string | undefined): number | null {
  const match = time ? /^(\d{2}):(\d{2})$/.exec(time) : null;
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

function reminder(key: string, at: number): Reminder {
  return { id: reminderId(`ju:${key}`), at, title: GENERIC_TITLE, body: PRIVATE_REMINDER };
}

/**
 * The reminders this tab asks the phone for: one on the morning of each open
 * plan (at 08:00, or an hour before an earlier time), and, when this phone
 * has "Remind me after N days" on, one evening N days after the last marked
 * day. Each re-sync replaces the last, so marking a day moves the reminder.
 * A reminder already past is not scheduled again, so it never nags.
 */
export function planPrivateReminders(view: Pick<BelovedView, 'together' | 'plans'>, now: number, today: Ymd, after: number): Reminder[] {
  const out: Reminder[] = [];
  const todayN = dayNumber(today);
  for (const plan of openPlans(view)) {
    const day = parseDay(plan.record.day);
    if (!day) continue;
    const ahead = dayNumber(day) - todayN;
    if (ahead < 0 || ahead > PLAN_HORIZON_DAYS) continue;
    const at = minutes(plan.record.time);
    const minute = at === null ? DAY_HOUR * 60 : Math.min(DAY_HOUR * 60, Math.max(0, at - 60));
    const when = new Date(day.y, day.m - 1, day.d, 0, minute, 0, 0).getTime();
    if (when > now) out.push(reminder(`plan:${plan.id}`, when));
  }
  const plans = out.sort((a, b) => a.at - b.at).slice(0, MAX_PLAN_REMINDERS);
  if (after > 0) {
    const days = markedDays(view, today);
    if (days.length) {
      const when = localMoment(fromDayNumber(days[days.length - 1] + after), EVE_HOUR);
      if (when > now) plans.push(reminder('after', when));
    }
  }
  return plans.sort((a, b) => a.at - b.at);
}
