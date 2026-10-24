/**
 * The coach's daily reminders: bedtime, water and a movement break.
 *
 * They are ordinary reminders in the timers pipeline (`/api/communication/timers`,
 * recurrence `daily`), so they ring on the person's phone and dashboard like any
 * other reminder, show under timers, and open the Health page when tapped. The
 * titles identify them; nothing else is stored.
 */
import type { TimerRecord } from '../types/api';

export type HealthReminderKind = 'bedtime' | 'water' | 'movement';

export interface HealthReminderDef {
  kind: HealthReminderKind;
  label: string;
  title: string;
  emoji: string;
  hint: string;
}

export const HEALTH_REMINDERS: HealthReminderDef[] = [
  {
    kind: 'bedtime',
    label: 'Bedtime',
    title: 'Bedtime: time to start winding down',
    emoji: '🌙',
    hint: 'About 30 minutes before you want to be asleep.',
  },
  { kind: 'water', label: 'Water', title: 'Water break: have a glass of water', emoji: '💧', hint: 'A mid-morning or mid-afternoon nudge.' },
  {
    kind: 'movement',
    label: 'Movement break',
    title: 'Movement break: stand up, stretch, walk for two minutes',
    emoji: '🚶',
    hint: 'A time you are usually sitting still.',
  },
];

/** The payload for the timers pipeline: a daily reminder at `HH:MM` that opens Health. */
export function reminderPayload(def: HealthReminderDef, clock: string) {
  return {
    title: def.title,
    type: 'reminder' as const,
    time_str: clock,
    recurrence: 'daily',
    scope: 'user' as const,
    emoji: def.emoji,
    url: '/fitness',
  };
}

/** The person's existing health reminders, by kind. */
export function existingReminders(timers: TimerRecord[] | undefined): Partial<Record<HealthReminderKind, TimerRecord>> {
  const out: Partial<Record<HealthReminderKind, TimerRecord>> = {};
  for (const def of HEALTH_REMINDERS) {
    const match = (timers ?? []).find((timer) => timer.type === 'reminder' && timer.title === def.title && timer.active !== false);
    if (match) out[def.kind] = match;
  }
  return out;
}
