/**
 * Reminders for the couple's special days, scheduled on the phone itself.
 *
 * The server cannot read a wedding date or a birthday, so it cannot remind
 * anyone of one. Instead, whenever Beloved is opened (unlocked) on the Android
 * app, the phone works out the coming reminders here and hands them to the
 * app's own alarm scheduler (plugins/belovedReminders.ts), replacing the last
 * set. Nothing about them goes to any server or push service.
 *
 * The text is generic unless this phone has "Show what the day is" turned on
 * (per phone, off by default, like the private chat's "Show message text in
 * notifications"): then it names the day. Even then the lock screen shows
 * only the generic line (the native side marks it private).
 */
import { dayNumber, fromDayNumber, type Ymd } from './dates';
import type { SpecialDay } from './days';

export interface Reminder {
  /** A stable positive 31-bit id, so a re-sync replaces rather than duplicates. */
  id: number;
  /** When to show it, in milliseconds since the epoch (the phone's local time). */
  at: number;
  title: string;
  body: string;
}

/** The generic text: says nothing about which day it is. */
export const GENERIC_TITLE = 'Jarvis';
export const GENERIC_TODAY = 'Something special today. Open Jarvis to see.';
export const GENERIC_SOON = 'Something special is coming up. Open Jarvis to see.';

/** Hours of the local day the reminders are shown: the evening before, then the morning of. */
export const EVE_HOUR = 19;
export const DAY_HOUR = 8;
/** How far ahead to schedule, and at most how many (Android limits pending alarms per app). */
export const HORIZON_DAYS = 120;
export const MAX_REMINDERS = 40;

const STORAGE_KEY = 'jarvis.beloved.reminderDetails';

/** This phone's "Show what the day is in reminders" choice (off unless turned on). */
export function reminderDetails(): boolean {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

export function setReminderDetails(on: boolean): void {
  try {
    if (on) globalThis.localStorage.setItem(STORAGE_KEY, '1');
    else globalThis.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Not kept: reminders stay generic.
  }
}

/** A stable 31-bit id from a string (FNV-1a), never 0. */
export function reminderId(key: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 1) || 1;
}

/** Local wall-clock time on a calendar day, in ms (the phone's own time zone). */
export function localMoment(day: Ymd, hour: number): number {
  return new Date(day.y, day.m - 1, day.d, hour, 0, 0, 0).getTime();
}

/**
 * The reminders to schedule now: the evening before and the morning of each
 * special day within the horizon, never in the past, soonest first.
 */
export function planReminders(days: SpecialDay[], now: number, details: boolean): Reminder[] {
  const out: Reminder[] = [];
  for (const day of days) {
    if (day.inDays > HORIZON_DAYS) continue;
    const eve = fromDayNumber(dayNumber(day.date) - 1);
    const moments: Array<{ at: number; when: 'eve' | 'day' }> = [
      { at: localMoment(eve, EVE_HOUR), when: 'eve' },
      { at: localMoment(day.date, DAY_HOUR), when: 'day' },
    ];
    for (const moment of moments) {
      if (moment.at <= now) continue;
      out.push({
        id: reminderId(`${day.key}:${moment.when}`),
        at: moment.at,
        title: details ? 'Beloved' : GENERIC_TITLE,
        body: details ? `${moment.when === 'eve' ? 'Tomorrow' : 'Today'}: ${day.name}` : moment.when === 'eve' ? GENERIC_SOON : GENERIC_TODAY,
      });
    }
  }
  return out.sort((a, b) => a.at - b.at).slice(0, MAX_REMINDERS);
}
