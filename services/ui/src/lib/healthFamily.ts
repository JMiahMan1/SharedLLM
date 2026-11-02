/**
 * Text for what family members shared with the viewer (Health → Today).
 *
 * The server projects a shared cycle as phase and next period only; this
 * turns that into one line and never asks for more.
 */
import type { HealthFamilyPerson } from '../types/api';
import { formatMinutes } from './healthHub';
import { fastClock } from './fastingClock';

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
 * A shared fast in one line: which fast, when it started, how long so far,
 * and the time left to its planned end; or the last one when none is running.
 */
export function sharedFastingLine(
  fasting: HealthFamilyPerson['fasting'] | undefined,
  now: Date = new Date(),
): string | null {
  if (!fasting) return null;
  const { active, last } = fasting;
  if (active) {
    const clock = fastClock(active, now);
    const parts = [`${active.kind_name ?? 'Fasting'} since ${clockTime(active.started_at, now)}`, `${formatMinutes(clock.elapsedSeconds / 60)} so far`];
    if (clock.remainingSeconds !== null) {
      parts.push(
        clock.reached
          ? 'complete'
          : active.days && clock.day
            ? `day ${Math.min(clock.day, active.days)} of ${active.days}`
            : `${formatMinutes(clock.remainingSeconds / 60)} left`,
      );
    }
    if (active.intention) parts.push(`praying for ${active.intention}`);
    return parts.join(' · ');
  }
  if (last?.ended_at) {
    return `Not fasting · last fast ${formatMinutes(last.elapsed_minutes)}, ended ${clockTime(last.ended_at, now)}`;
  }
  return 'Not fasting';
}
