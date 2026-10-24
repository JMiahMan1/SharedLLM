/**
 * Time and date formatting for every media surface (BUG-41, UX-36).
 *
 * One copy, so the page, the sheets, the mini player and the dashboard widget
 * cannot disagree about how a ten-hour audiobook reads.
 */

/** What an unknown time shows: never a fake "0:00". */
export const UNKNOWN_TIME = '--:--';

function seconds(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** `M:SS` below an hour, `H:MM:SS` from an hour; `--:--` when unknown. */
export function formatDuration(value: unknown): string {
  const n = seconds(value);
  if (n === null) return UNKNOWN_TIME;
  const total = Math.floor(n);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** `-3:12` for what is left, or `--:--` when the length is unknown. */
export function formatRemaining(position: unknown, duration: unknown): string {
  const d = seconds(duration);
  if (d === null || d === 0) return UNKNOWN_TIME;
  const p = seconds(position) ?? 0;
  return `-${formatDuration(Math.max(0, d - p))}`;
}

/** "2h 13m left" / "13m left" / "Under a minute left" for long-form items. */
export function formatTimeLeft(position: unknown, duration: unknown): string | null {
  const d = seconds(duration);
  if (d === null || d === 0) return null;
  const left = Math.max(0, d - (seconds(position) ?? 0));
  const mins = Math.floor(left / 60);
  if (mins < 1) return 'Under a minute left';
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m left` : `${m}m left`;
}

/** Screen-reader time: "1 hour 2 minutes 5 seconds" (aria-valuetext). */
export function spokenDuration(value: unknown): string {
  const n = seconds(value);
  if (n === null) return 'unknown';
  const total = Math.floor(n);
  const parts: string[] = [];
  const unit = (count: number, word: string) => {
    if (count > 0) parts.push(`${count} ${word}${count === 1 ? '' : 's'}`);
  };
  unit(Math.floor(total / 3600), 'hour');
  unit(Math.floor((total % 3600) / 60), 'minute');
  unit(total % 60, 'second');
  return parts.length ? parts.join(' ') : '0 seconds';
}

/** Normalize a `last_played` value (epoch s, epoch ms or ISO string) to epoch ms. */
export function normalizePlayedAt(value: unknown): number | undefined {
  if (value === undefined || value === null || value === '') return undefined;
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isNaN(n)) return n <= 0 ? undefined : n > 1e12 ? n : n * 1000;
  const parsed = Date.parse(String(value));
  return Number.isNaN(parsed) ? undefined : parsed;
}

/** "Just now", "5m ago", "3h ago", "2d ago", then a short date. */
export function formatTimeAgo(ts: number, now: number = Date.now()): string {
  const mins = Math.floor((now - ts) / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
