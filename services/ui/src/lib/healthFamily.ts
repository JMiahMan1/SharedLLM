/**
 * Text for what family members shared with the viewer (Health → Today).
 *
 * The server projects a shared cycle as phase and next period only; this
 * turns that into one line and never asks for more.
 */
import type { HealthFamilyPerson } from '../types/api';
import { formatMinutes } from './healthHub';

export function sharedCycleLine(cycle: HealthFamilyPerson['cycle'] | undefined): string | null {
  if (!cycle?.enabled || !cycle.phase_label) return null;
  const parts = [`Cycle: ${cycle.phase_label}`];
  const days = cycle.days_until_next_period;
  if (days !== null) {
    parts.push(days >= 0 ? `next period in ${days} day${days === 1 ? '' : 's'}` : 'period expected');
  }
  return parts.join(' · ');
}

/** "8:10 PM", or "Tue 8:10 PM" when it was not today. */
function clockTime(iso: string, now: Date): string {
  const at = new Date(iso);
  const time = at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return at.toDateString() === now.toDateString()
    ? time
    : `${at.toLocaleDateString([], { weekday: 'short' })} ${time}`;
}

/**
 * A shared fast in one line: when it started, how long so far, and the time
 * left to the owner's own target; or the last one when none is running.
 */
export function sharedFastingLine(
  fasting: HealthFamilyPerson['fasting'] | undefined,
  now: Date = new Date(),
): string | null {
  if (!fasting) return null;
  const { active, last } = fasting;
  if (active) {
    const parts = [`Fasting since ${clockTime(active.started_at, now)}`, `${formatMinutes(active.elapsed_minutes)} so far`];
    if (active.target_hours) {
      parts.push(
        active.reached
          ? `${active.target_hours}h goal reached`
          : `${formatMinutes(active.remaining_minutes)} left of ${active.target_hours}h`,
      );
    }
    return parts.join(' · ');
  }
  if (last?.ended_at) {
    return `Not fasting · last fast ${formatMinutes(last.elapsed_minutes)}, ended ${clockTime(last.ended_at, now)}`;
  }
  return 'Not fasting';
}
