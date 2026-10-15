/**
 * Shared TanStack Query keys for the Wander surface.
 *
 * Same reasoning as `healthQueries.ts`: the dashboard card and the page read
 * the same endpoints, and two different keys mean two caches, which is how the
 * two screens come to disagree about the same trip.
 *
 * `tripsQueryKey` deliberately carries no user id. `/api/geo/trips` is scoped
 * by *the caller's own* opt-in consent, so passing an explicit `user_id` would
 * bypass that and let one account request another's trips.
 */

import { api } from '../services/api';

export const TRIPS_STALE_MS = 60_000;

/**
 * Takes a user id only so call sites can be explicit about who they *think*
 * they are asking about; it is deliberately not part of the key. Adding it
 * would let a stale key serve one account's trips to another after a sign-in
 * change, and trips are already scoped server-side by the caller's consent.
 */
export function tripsQueryKey(): string[] {
  return ['geo-trips'];
}

export function vehiclesQueryKey() {
  return ['geo-vehicles'] as const;
}

export function geoPeopleQueryKey() {
  return ['geo-people'] as const;
}

export function geoZonesQueryKey() {
  return ['geo-zones'] as const;
}

/**
 * Everyone's latest phone fix (identity's /api/users/location/all): one query,
 * one cache, one request every 30 s, shared by the Wander map, the member
 * cards and the Presence widget. Each derives its own view with `select`, so
 * they can no longer disagree about where someone is.
 */
export const userLocationsQuery = {
  queryKey: ['user-locations'] as const,
  queryFn: () => api.getAllUserLocations(),
  refetchInterval: 30_000,
  staleTime: 15_000,
  retry: false,
} as const;

export const tripsQueryOptions = {
  staleTime: TRIPS_STALE_MS,
  retry: false,
} as const;

/** HA-derived house config; changes rarely, so it can be cached longer. */
export const houseConfigQueryOptions = {
  staleTime: 5 * 60_000,
  retry: false,
} as const;