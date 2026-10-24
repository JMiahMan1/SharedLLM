/**
 * Weekly family challenge text: the leaderboard lines and the chat message.
 *
 * Counts are days that the member's own logs counted; nobody's calories or
 * weight ever appear, so the same message is fine in a chat with the kids.
 */

import type { HealthChallenge } from '../types/api';
import { formatDayLabel } from './healthHub';

/** Who a username is in the family directory, falling back to the username itself. */
export type NameOf = (userId: string) => string;

export const plainName: NameOf = (userId) => userId;

/** "3 of 5 days" for one standing. */
export function standingText(count: number, target: number, unit: string): string {
  return `${count} of ${target} ${unit}`;
}

/** The chat message a shared challenge becomes. */
export function challengeShareText(challenge: HealthChallenge, nameOf: NameOf = plainName, note?: string): string {
  const head = `🏆 Family challenge: ${challenge.title} — ${challenge.target_days} ${challenge.unit} this week (${formatDayLabel(challenge.week_start)} – ${formatDayLabel(challenge.week_end)})`;
  const lines = challenge.leaderboard.map(
    (row, index) =>
      `${index + 1}. ${nameOf(row.user_id)} · ${standingText(row.count, challenge.target_days, challenge.unit)}${row.reached ? ' ✓' : ''}`,
  );
  const body = lines.length > 0 ? `${head}\n${lines.join('\n')}` : `${head}\nNobody has joined yet.`;
  const withJoin = `${body}\nJoin it on Health → Today.`;
  const intro = (note ?? '').trim();
  return intro ? `${intro}\n\n${withJoin}` : withJoin;
}

/** The short preview line the share sheet shows. */
export function challengeSharePreview(challenge: HealthChallenge): string {
  const joined = challenge.leaderboard.length;
  return `${challenge.title}: ${challenge.target_days} ${challenge.unit} this week · ${joined} joined`;
}
