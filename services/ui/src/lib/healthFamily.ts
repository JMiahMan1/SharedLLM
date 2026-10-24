/**
 * Text for what family members shared with the viewer (Health → Today).
 *
 * The server projects a shared cycle as phase and next period only; this
 * turns that into one line and never asks for more.
 */
import type { HealthFamilyPerson } from '../types/api';

export function sharedCycleLine(cycle: HealthFamilyPerson['cycle'] | undefined): string | null {
  if (!cycle?.enabled || !cycle.phase_label) return null;
  const parts = [`Cycle: ${cycle.phase_label}`];
  const days = cycle.days_until_next_period;
  if (days !== null) {
    parts.push(days >= 0 ? `next period in ${days} day${days === 1 ? '' : 's'}` : 'period expected');
  }
  return parts.join(' · ');
}
