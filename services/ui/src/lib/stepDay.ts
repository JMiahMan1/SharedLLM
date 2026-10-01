/**
 * Which local calendar day a step reading belongs to.
 *
 * ## Why this exists
 *
 * The daily reset used to be a 30-second interval looking for exactly
 * `getHours() === 0 && getMinutes() === 0` — a 60-second-wide window. If the
 * app was backgrounded, killed, or simply not open at 00:00, the reset never
 * ran and yesterday's total stayed on screen as today's.
 *
 * Comparing an actual date key instead of a clock window closes that hole: the
 * rollover is detected whenever the next reading is taken, no matter how long
 * the app was closed in between. It also matches how the OS pedometer buckets
 * its own "today" total, which is local-day based.
 *
 * Local time is deliberate and matches the device's own calendar — a user
 * thinks in their own midnight, not UTC.
 */

/** `YYYY-MM-DD` in the device's local time zone. */
export function localDayKey(date: Date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * True when `now` falls on a different local day than `lastDayKey`.
 *
 * A `null`/absent previous key counts as a rollover, which is what forces a
 * fresh read after a cold start rather than trusting whatever was in memory.
 */
export function hasDayRolledOver(lastDayKey: string | null, now: Date = new Date()): boolean {
  return lastDayKey !== localDayKey(now);
}

/** Whole days between two local day keys, or `null` if either is unusable. */
export function daysBetween(fromDayKey: string, toDayKey: string): number | null {
  const from = Date.parse(`${fromDayKey}T00:00:00`);
  const to = Date.parse(`${toDayKey}T00:00:00`);
  if (Number.isNaN(from) || Number.isNaN(to)) return null;
  return Math.round((to - from) / 86_400_000);
}