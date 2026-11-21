import { api } from '../services/api';
import type { UnlockRequest } from '../types/api';

/** The lengths a parent can give with one tap; the minutes asked are always offered too. */
export const UNLOCK_MINUTE_CHOICES = [15, 30, 60, 120];

const REASON_WORDS: Record<string, string> = {
  bedtime: ' at bedtime',
  limit: " after today's limit",
  paused: ' while it is paused',
  school: ' during school hours',
  chores: " before today's chores",
};

/** "Asks to unlock their PC for 30 minutes at bedtime". */
export function unlockLine(r: UnlockRequest): string {
  return `Asks to unlock their ${r.device} for ${r.minutes_asked} minutes${REASON_WORDS[r.lock_reason] ?? ''}`;
}

/** "expires in 7 min", from the server's expiry. */
export function expiresIn(expiresAt: string, nowMs: number = Date.now()): string {
  const left = Math.ceil((new Date(expiresAt).getTime() - nowMs) / 60_000);
  return left <= 0 ? 'expiring now' : `expires in ${left} min`;
}

/** "30 min", "1 h", "2 h". */
export function minutesLabel(m: number): string {
  return m < 60 ? `${m} min` : `${m / 60} h`;
}

export const unlockRequestsQuery = {
  queryKey: ['unlock-requests', 'pending'] as const,
  queryFn: async () => (await api.getUnlockRequests('pending')).requests,
  // A request expires in ten minutes: look often enough to answer it in time.
  refetchInterval: 15_000,
  retry: false,
};
