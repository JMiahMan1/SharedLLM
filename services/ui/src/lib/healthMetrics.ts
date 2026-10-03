/**
 * Pure derivations for the Health page.
 *
 * Kept out of the page component for two reasons: the arithmetic is where the
 * bugs actually live, and a component file cannot export non-components under
 * this project's `react-refresh/only-export-components` rule.
 */

import type { StepsResponse, Workout } from '../types/api';

export const DEFAULT_STEP_GOAL = 10000;

/** `StepsResponse.goal` is declared `number`, but geo's no-redis early return
 * omits it entirely, so it can genuinely be undefined at runtime. */
type PartialSteps = Partial<StepsResponse> | null | undefined;

const finite = (value: unknown, fallback: number): number => {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
};

export interface StepsSummary {
  /** Steps logged today. Never negative, never NaN. */
  today: number;
  goal: number;
  /** 0-100, rounded. Capped so an overachiever does not overflow a ring. */
  percent: number;
  remaining: number;
  reachedGoal: boolean;
  /** False when the pedometer has never reported -- lets the page say so
   * rather than showing a confident zero. */
  hasData: boolean;
}

export function stepsSummary(steps: PartialSteps): StepsSummary {
  const daily = steps?.daily_steps ?? {};
  const today = finite(steps?.today, 0);
  const goal = finite(steps?.goal, DEFAULT_STEP_GOAL) || DEFAULT_STEP_GOAL;
  const percent = Math.min(100, Math.round((today / goal) * 100));
  return {
    today,
    goal,
    percent,
    remaining: Math.max(0, goal - today),
    reachedGoal: today >= goal,
    hasData: Object.keys(daily).length > 0,
  };
}

export interface DailyStepPoint {
  /** `YYYY-MM-DD`. */
  date: string;
  steps: number;
  /** Single-letter weekday (`M`, `T`, …) for a compact chart axis. */
  weekday: string;
}

/**
 * The last `days` buckets, oldest first, **sorted by date**.
 *
 * Sorting is not cosmetic. The buckets arrive as a Redis hash, and hash field
 * order is explicitly undefined -- so the previous `Object.entries(...).slice(-7)`
 * could render last week in any order and silently label the wrong day. Sparse
 * days (the phone was asleep, which is exactly the failure this page exists to
 * surface) are kept as explicit zeros rather than dropped, so a gap reads as a
 * gap instead of compressing the axis.
 */
export function dailyStepSeries(
  dailySteps: Record<string, number> | null | undefined,
  days = 7,
  todayKey?: string,
): DailyStepPoint[] {
  const source = dailySteps ?? {};
  const keys = Object.keys(source)
    .filter((key) => /^\d{4}-\d{2}-\d{2}$/.test(key))
    .sort();

  const end = todayKey ?? keys[keys.length - 1];
  if (!end) return [];

  // Walk backwards `days - 1` days so the axis is continuous.
  const wanted: string[] = [];
  const cursor = new Date(`${end}T12:00:00`);
  for (let i = days - 1; i >= 0; i -= 1) {
    const d = new Date(cursor);
    d.setDate(cursor.getDate() - i);
    wanted.push(d.toISOString().slice(0, 10));
  }

  return wanted.map((date) => ({
    date,
    steps: finite(source[date], 0),
    weekday: new Date(`${date}T12:00:00`).toLocaleDateString(undefined, { weekday: 'narrow' }),
  }));
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** Long-form label for a `YYYY-MM-DD` key, for tooltips and `aria-label`s. */
export function stepDayLabel(date: string): string {
  const parsed = new Date(`${date}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return `${WEEKDAYS[parsed.getDay()]} ${parsed.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  })}`;
}

/**
 * Which devices contributed today's steps, e.g. `Phone + watch`.
 *
 * Geo returns a per-source breakdown precisely so the UI can explain a number
 * that does not match what one device counted; an empty map means the pedometer
 * has not reported today.
 */
export function stepSourcesLabel(
  sources: Record<string, number> | null | undefined,
): string | null {
  const entries = Object.entries(sources ?? {})
    .filter(([, value]) => Number(value) > 0)
    .sort((a, b) => a[0].localeCompare(b[0]));
  if (entries.length === 0) return null;
  const names = entries.map(([source]) => source);
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(', ')} + ${names[names.length - 1]}`;
}

/**
 * Average pace in min/mile, or null when it cannot be computed.
 *
 * Null rather than 0: a zero-duration workout has no pace, and rendering
 * `0:00 /mi` would read as "impossibly fast" rather than "not measurable".
 */
export function paceLabel(distanceMiles: number | null | undefined, durationSeconds: number | null | undefined): string | null {
  const miles = Number(distanceMiles);
  const seconds = Number(durationSeconds);
  if (!Number.isFinite(miles) || miles <= 0) return null;
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  const secondsPerMile = seconds / miles;
  if (!Number.isFinite(secondsPerMile)) return null;
  const minutes = Math.floor(secondsPerMile / 60);
  const remainder = Math.round(secondsPerMile % 60);
  // Rounding 59.6s up must carry into the minute, or 0:60 is printed.
  if (remainder === 60) return `${minutes + 1}:00`;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

export interface WorkoutTotals {
  count: number;
  miles: number;
  minutes: number;
  steps: number;
}

export function workoutTotals(workouts: Workout[] | null | undefined): WorkoutTotals {
  const list = workouts ?? [];
  return {
    count: list.length,
    miles: list.reduce((sum, w) => sum + finite(w.distance_miles, 0), 0),
    minutes: Math.round(list.reduce((sum, w) => sum + finite(w.duration_seconds, 0), 0) / 60),
    steps: list.reduce((sum, w) => sum + finite(w.steps, 0), 0),
  };
}

export function formatMiles(miles: number): string {
  const n = finite(miles, 0);
  return n < 10 ? n.toFixed(1) : Math.round(n).toLocaleString();
}

export function formatCount(value: number): string {
  return finite(value, 0).toLocaleString();
}

/**
 * A 0-23 hour as a person says it: "9am", "12pm", "11pm".
 *
 * Hours are the *phone's* local hours, so the caller must not re-derive them
 * from the browser's clock — that mismatch is what makes a traveller's chart
 * disagree with the rest of their day. Midnight is 12am, not 0am.
 */
export function hourOfDay(hour: number): string {
  const h = Math.round(finite(hour, 0));
  const clamped = h < 0 ? 0 : h > 23 ? 23 : h;
  const suffix = clamped < 12 ? 'am' : 'pm';
  const twelve = clamped % 12 === 0 ? 12 : clamped % 12;
  return `${twelve}${suffix}`;
}

/** A running workout's elapsed time, floored at zero so a clock skew or a
 * server timestamp slightly in the future cannot render `-0:04`. */
export function elapsedLabel(startTimeSeconds: number, nowSeconds: number): string {
  const elapsed = Math.max(0, finite(nowSeconds, 0) - finite(startTimeSeconds, 0));
  const total = Math.floor(elapsed);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}
/**
 * How long ago the phone last uploaded, and whether that is recent enough to
 * trust.
 *
 * `last_synced` is epoch seconds. It is genuinely absent (null) when a user has
 * never synced, which is a different state from "synced long ago" and must not
 * be rendered as the same thing.
 */
export type SyncFreshness = 'never' | 'fresh' | 'aging' | 'stale' | 'unknown';

export interface SyncStatus {
  freshness: SyncFreshness;
  /** e.g. `2m ago`, `3h ago`. Empty for `never`. */
  label: string;
  /** True when the number on screen should not be trusted as current. */
  stale: boolean;
}

/** A pedometer that has not spoken in this long is not describing today. */
const AGING_AFTER_MS = 20 * 60_000;
const STALE_AFTER_MS = 2 * 60 * 60_000;

export function syncStatus(
  lastSynced: number | null | undefined,
  nowMs: number = Date.now(),
): SyncStatus {
  if (lastSynced == null || !Number.isFinite(Number(lastSynced))) {
    return { freshness: 'never', label: '', stale: true };
  }
  const ageMs = nowMs - Number(lastSynced) * 1000;
  // A timestamp in the future means clock skew, not a fresh reading; treat it
  // as fresh rather than printing a negative age.
  if (ageMs <= 0) return { freshness: 'fresh', label: 'just now', stale: false };

  const minutes = Math.floor(ageMs / 60_000);
  const hours = Math.floor(ageMs / 3_600_000);
  const days = Math.floor(ageMs / 86_400_000);
  const label = days >= 1 ? `${days}d ago` : hours >= 1 ? `${hours}h ago` : `${Math.max(1, minutes)}m ago`;

  const freshness: SyncFreshness =
    ageMs >= STALE_AFTER_MS ? 'stale' : ageMs >= AGING_AFTER_MS ? 'aging' : 'fresh';
  return { freshness, label, stale: ageMs >= STALE_AFTER_MS };
}

/** One line explaining what a non-fresh sync means, or null when fresh. */
export function syncAdvice(status: SyncStatus): string | null {
  switch (status.freshness) {
    case 'never':
      return 'No sync yet — open the app on your phone';
    case 'stale':
      return `Not updated since ${status.label} — open the app on your phone`;
    case 'aging':
      return null;
    default:
      return null;
  }
}
