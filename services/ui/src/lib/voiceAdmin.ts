import type { NewVoiceSeen } from '../types/api';

/** Helpers for Admin > Users & Devices voice panels (kept out of the component files for fast refresh). */

export function satelliteName(entity: string | null): string {
  if (!entity) return 'a satellite';
  return entity.replace(/^[a-z_]+\./, '').replace(/_?assist_satellite$/, '').replace(/_/g, ' ').trim() || 'a satellite';
}

/** "a new voice talked to the Office Echo 5 times this week" */
export function newVoiceLine(v: NewVoiceSeen): string {
  const label = v.label?.trim();
  const where = label ? `the ${label}` : v.device.includes('.') ? `the ${satelliteName(v.device)}` : 'a satellite';
  const times = v.visits_week === 1 ? 'once' : `${v.visits_week} times`;
  return `A new voice talked to ${where} ${times} this week.`;
}

/** How much quieter than usual counts as a whisper: fewer dB catches softer whispers, more is stricter. */
export const SENSITIVITY: { label: string; db: number }[] = [
  { label: 'Catch soft whispers too', db: 5 },
  { label: 'Normal', db: 8 },
  { label: 'Only clear whispers', db: 12 },
];
