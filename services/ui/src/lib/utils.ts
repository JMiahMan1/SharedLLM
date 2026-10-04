import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(value?: string | null): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatDateTime(value?: string | null): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/**
 * Media elapsed/duration clock. Audiobooks and podcast episodes run past an
 * hour, so anything an hour or longer grows an hours field: `1:06:40`, never
 * `66:40`. Matches the convention AmbientTimerWidget already used.
 *
 * One implementation for every media surface: the Media page, the local audio
 * player and the dashboard's active-media widget previously each carried their
 * own copy, two of which rolled 90 minutes into `90:00`.
 */
export function formatClock(seconds?: number | string | null): string {
  const n = Number(seconds);
  if (!Number.isFinite(n)) return '0:00';
  const total = Math.max(0, Math.floor(n));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}
