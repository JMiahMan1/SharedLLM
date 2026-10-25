/**
 * The health card a person shares to a family chat: their day in a few lines.
 *
 * Only what the person's own cards show and nothing private: sleep, water,
 * steps and the check-in's energy. Never calories, weight or cycle data -- a
 * family chat includes the kids, and food numbers there would be pressure.
 */
import { CHECKIN_WORDS, formatMinutes } from './healthHub';
import type { HealthSummary } from '../types/api';
import { withShareNote } from './verseShare';

export interface ShareableDay {
  summary: HealthSummary;
  /** Today's steps from geo, or null when the phone has not reported. */
  steps: number | null;
}

/** The lines of the card, in display order; empty when nothing is logged yet. */
export function healthDayLines({ summary, steps }: ShareableDay): string[] {
  const on = (key: keyof HealthSummary['profile']['metrics']) => summary.profile.metrics?.[key] === true;
  const lines: string[] = [];
  const night = summary.sleep.last_night;
  if (on('sleep') && night) {
    lines.push(`😴 Slept ${formatMinutes(night.duration_minutes)}${summary.sleep.score ? ` (${summary.sleep.score.label})` : ''}`);
  }
  if (on('steps') && steps !== null && steps > 0) lines.push(`👟 ${steps.toLocaleString()} steps so far`);
  if (on('water') && summary.water.logged) {
    lines.push(`💧 ${summary.water.ml.toLocaleString()} ml of water${summary.water.percent !== null && summary.water.percent >= 100 ? ' - goal reached' : ''}`);
  }
  const energy = summary.checkin?.energy;
  if (on('checkin') && energy) lines.push(`⚡ Feeling ${CHECKIN_WORDS[energy].toLowerCase()}`);
  return lines;
}

/** The chat message: an optional note, then the card. */
export function healthDayShareText(day: ShareableDay, note?: string): string {
  const lines = healthDayLines(day);
  const body = lines.length ? `💚 My day so far:\n${lines.join('\n')}` : '💚 Just getting started today.';
  return withShareNote(body, note);
}
