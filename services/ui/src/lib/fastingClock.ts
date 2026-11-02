/**
 * A running fast as a live clock.
 *
 * The server says when the fast started and when it is planned to end; the
 * card counts between the two every second, so the time left falls steadily
 * instead of jumping once a minute. Times are shown in the device's own zone
 * (the server stores UTC instants).
 */
import type { HealthFast } from '../types/api';

export interface FastClock {
  elapsedSeconds: number;
  /** Null when the fast has no planned end. */
  remainingSeconds: number | null;
  /** 0-100, floored, so it never reads 100 before the end. */
  percent: number | null;
  reached: boolean;
  /** Day of a season (1-based), when the fast is a season of days. */
  day: number | null;
  ends: Date | null;
}

export function fastClock(fast: HealthFast, now: Date = new Date()): FastClock {
  const started = new Date(fast.started_at).getTime();
  const ends = fast.ends_at
    ? new Date(fast.ends_at)
    : fast.target_hours
      ? new Date(started + fast.target_hours * 3_600_000)
      : null;
  const until = fast.ended_at ? new Date(fast.ended_at).getTime() : now.getTime();
  const elapsedSeconds = Math.max(0, Math.floor((until - started) / 1000));
  if (!ends) return { elapsedSeconds, remainingSeconds: null, percent: null, reached: false, day: null, ends: null };
  const total = Math.max(1, (ends.getTime() - started) / 1000);
  const remainingSeconds = Math.max(0, Math.ceil((ends.getTime() - until) / 1000));
  return {
    elapsedSeconds,
    remainingSeconds,
    percent: Math.min(100, Math.floor((elapsedSeconds * 100) / total)),
    reached: remainingSeconds === 0,
    day: fast.days ? Math.floor(elapsedSeconds / 86_400) + 1 : null,
    ends,
  };
}

/** `5:04:09` under a day, `2d 5h` beyond it. */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const days = Math.floor(s / 86_400);
  const hours = Math.floor((s % 86_400) / 3600);
  if (days) return `${days}d ${hours}h`;
  const minutes = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

/** "3:00 PM" today, "Wed 3:00 PM" another day this week, "Dec 24, 3:00 PM" further off. */
export function localWhen(value: string | Date, now: Date = new Date()): string {
  const at = typeof value === 'string' ? new Date(value) : value;
  const time = at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (at.toDateString() === now.toDateString()) return time;
  const days = Math.abs(at.getTime() - now.getTime()) / 86_400_000;
  if (days < 6) return `${at.toLocaleDateString([], { weekday: 'short' })} ${time}`;
  return `${at.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`;
}
