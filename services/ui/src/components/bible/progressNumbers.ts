import type { BiblePlanEnrollment } from '../../types/api';

/** A date as the reader's own calendar names it, YYYY-MM-DD. */
export function localDayKey(day: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

/**
 * Chapters finished over the last seven calendar days, today included, in the
 * reader's local dates. Looked up by date, so a day with no reading (missing
 * from the window) counts as nothing and the window's order never matters.
 */
export function chaptersThisWeek(perDay: Record<string, number> | undefined, now: Date = new Date()): number {
  if (!perDay) return 0;
  let total = 0;
  for (let back = 0; back < 7; back += 1) {
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() - back);
    total += perDay[localDayKey(day)] ?? 0;
  }
  return total;
}

/**
 * The plan to show progress for: one still under way before a finished one;
 * among those, the plan the reader last opened, else the one started last.
 */
export function pickCurrentPlan(mine: BiblePlanEnrollment[] | undefined, lastPlanId?: string | null): BiblePlanEnrollment | null {
  if (!mine?.length) return null;
  const open = mine.filter((p) => !p.finished);
  const pool = open.length ? open : mine;
  const last = lastPlanId ? pool.find((p) => p.plan_id === lastPlanId) : undefined;
  if (last) return last;
  return [...pool].sort((a, b) => b.started_on.localeCompare(a.started_on))[0];
}
