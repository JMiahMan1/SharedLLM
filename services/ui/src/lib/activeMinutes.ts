/** Words for the weekly active-minutes card and the dashboard's health card. */
import type { HealthActiveMinutes } from '../types/api';

export const SOURCE_LABELS: Record<HealthActiveMinutes['sources'][number], string> = {
  device: 'your watch',
  heart_rate: 'heart-rate zones',
  workouts: 'workouts',
  steps: 'brisk-walking hours (estimate)',
};

/** One line: where the week stands against the person's own target. */
export function activeMinutesLine(week: HealthActiveMinutes): string {
  if (week.total_minutes === null) return 'Nothing recorded this week yet.';
  const minutes = `${week.total_minutes} active minute${week.total_minutes === 1 ? '' : 's'}`;
  if (!week.target_minutes) return `${minutes} so far this week.`;
  if (week.total_minutes >= week.target_minutes) return `${minutes}: your ${week.target_minutes}-minute week is done.`;
  return `${minutes} of ${week.target_minutes} · ${week.target_minutes - week.total_minutes} to go this week.`;
}
