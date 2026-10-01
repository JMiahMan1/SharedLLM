/**
 * Shared TanStack Query keys and option presets for the health/steps surface.
 *
 * These exist because the Health page and the dashboard Health card used to
 * read the same endpoints under *different* keys. Two keys means two caches
 * means the card and the page can show different numbers at the same moment --
 * which is exactly the "is this thing even updating?" doubt the redesign is
 * meant to remove. Both sides now import from here so they share one entry.
 */

/** Polling cadence for pedometer data. The phone syncs on a 30s timer, so
 * anything much faster just re-reads the same number. */
export const STEPS_POLL_MS = 60_000;

/** Trips/workouts move on a human timescale; no need to poll at all. */
export const DEFAULT_STALE_MS = 60_000;

export function stepsQueryKey(userId?: string | null) {
  return ['daily-steps', (userId || 'me').toLowerCase()] as const;
}

export function workoutsQueryKey(userId?: string | null, limit = 10) {
  return ['workouts', (userId || 'me').toLowerCase(), limit] as const;
}

export function activeWorkoutQueryKey(userId?: string | null) {
  return ['workout-active', (userId || 'me').toLowerCase()] as const;
}

export function achievementsQueryKey(userId?: string | null) {
  return ['achievements', userId || 'me'] as const;
}

export function starsQueryKey(userId?: string | null) {
  return ['stars', userId || 'me'] as const;
}

/**
 * Options for anything backed by the phone's pedometer.
 *
 * `retry: false` matters more than it looks: the default is 3 retries, so a
 * phone that is simply asleep produces four failed requests per poll interval
 * forever. Absence of a pedometer is a normal state here, not a fault.
 */
export const pedometerQueryOptions = {
  staleTime: 30_000,
  refetchInterval: STEPS_POLL_MS,
  retry: false,
  refetchOnWindowFocus: true,
} as const;