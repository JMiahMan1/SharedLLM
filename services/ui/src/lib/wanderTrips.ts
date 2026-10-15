/**
 * Pure derivations for the Wander page.
 *
 * Kept out of the component for the same reasons as `healthMetrics`: the logic
 * is where the bugs live, and a component file cannot export non-components
 * under `react-refresh/only-export-components`.
 */

import type { Trip, TripLocation } from '../types/api';

/**
 * The username portion of a trip's owner id.
 *
 * Geo stores `user_id` as a prefixed form (`person.jeremiah`), so comparisons
 * have to normalise both sides rather than string-compare.
 */
export function tripOwner(trip: Pick<Trip, 'user_id'>): string {
  return (trip.user_id || '').split('.').pop()?.toLowerCase() ?? '';
}

/**
 * Can this user edit or share the trip?
 *
 * Only the owner may. The previous version also granted this to a user
 * literally named `admin` -- which does not exist here (the admin is
 * `jeremiah`), so the branch was dead code that only ever produced a 403 toast
 * from geo. An admin is still *not* the owner of someone else's trip, so
 * ownership is deliberately not widened to admins: the family circle is for
 * seeing where people went, not for rewriting their trips.
 */
export function isTripOwner(trip: Pick<Trip, 'user_id'>, currentUsername: string | undefined): boolean {
  const owner = tripOwner(trip);
  if (!owner || !currentUsername) return false;
  return owner === currentUsername.toLowerCase();
}

export function displayName(trip: Pick<Trip, 'user_name' | 'user_id'>): string {
  if (trip.user_name) return trip.user_name;
  const owner = tripOwner(trip);
  return owner ? owner.charAt(0).toUpperCase() + owner.slice(1) : 'Someone';
}

export type TripFilter = 'all' | 'mine';

export function filterTrips(
  trips: Trip[],
  filter: TripFilter,
  currentUsername: string | undefined,
): Trip[] {
  if (filter !== 'mine') return trips;
  if (!currentUsername) return [];
  return trips.filter((t) => tripOwner(t) === currentUsername.toLowerCase());
}

export interface TripStats {
  miles: number;
  cost: number;
  fuel: number;
  count: number;
  sharedCount: number;
}

/**
 * Aggregates over the trips currently on screen.
 *
 * Fuel and cost count **driving only** -- a walking trip has an
 * `activity_type` but no fuel economy, so including it would invent a cost.
 */
export function tripStats(trips: Trip[]): TripStats {
  let miles = 0;
  let cost = 0;
  let fuel = 0;
  let sharedCount = 0;

  for (const trip of trips) {
    miles += num(trip.distance_miles);
    if ((trip.activity_type || 'driving') === 'driving') {
      cost += num(trip.trip_cost_usd);
      fuel += num(trip.fuel_used_gal);
    }
    if (trip.is_shared) sharedCount += 1;
  }

  return { miles, cost, fuel, count: trips.length, sharedCount };
}

function num(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Round away binary-float noise for display.
 *
 * Summing per-trip floats lands on values like 2.6999999999999997, which
 * rendered raw in the summary tiles. Rounding for *display* only -- the totals
 * themselves stay exact so the tiles are not built on lossy sums.
 */
export function roundForDisplay(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.round(value * 10) / 10;
}

/** Fuel for the summary tile: at most one decimal, never a long float tail. */
export function formatFuelSummary(gallons: number): string {
  const rounded = roundForDisplay(gallons);
  return `${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)} gal`;
}

/** Dollars for the summary tile, matching formatFuelSummary's precision. */
export function formatCostSummary(usd: number): string {
  const rounded = roundForDisplay(usd);
  return `$${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)}`;
}

/**
 * Render a trip endpoint: resolved place name, else coordinates, else zone,
 * else a generic label. Geo writes `latitude`/`longitude`; older records use
 * `lat`/`lon`.
 */
export function formatTripLocation(loc: TripLocation | undefined, fallback: string): string {
  if (loc?.name) return loc.name;
  const lat = loc?.latitude ?? loc?.lat;
  const lon = loc?.longitude ?? loc?.lon;
  if (typeof lat === 'number' && typeof lon === 'number' && Number.isFinite(lat) && Number.isFinite(lon)) {
    return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  }
  return loc?.zone || fallback;
}

/** Coordinates for a trip endpoint, tolerating the legacy spelling. */
export function tripLocationCoords(loc?: TripLocation): { lat: number; lon: number } | null {
  const lat = loc?.latitude ?? loc?.lat;
  const lon = loc?.longitude ?? loc?.lon;
  if (typeof lat !== 'number' || typeof lon !== 'number') return null;
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { lat, lon };
}

/** Compact "5 min ago" label for a last-updated timestamp. */
export function relativeTime(iso: string | number | undefined, nowMs: number = Date.now()): string {
  const then = typeof iso === 'number' ? (iso < 1e12 ? iso * 1000 : iso) : new Date(iso ?? '').getTime();
  if (!Number.isFinite(then)) return '';
  const mins = Math.round((nowMs - then) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return hours === 1 ? '1 hr ago' : `${hours} hrs ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

/** Distance suffix for a nearby-place suggestion chip, e.g. ` · 120 m`. */
export function formatDistanceMeters(meters?: number): string {
  if (typeof meters !== 'number' || !Number.isFinite(meters)) return '';
  if (meters >= 1609.34) return ` · ${(meters / 1609.34).toFixed(1)} mi`;
  return ` · ${Math.round(meters)} m`;
}

export function formatMiles(miles: number): string {
  const n = num(miles);
  return n < 10 ? n.toFixed(1) : Math.round(n).toLocaleString();
}

export function formatMoney(value: number): string {
  return `$${num(value).toFixed(2)}`;
}

/**
 * A trip is only safe to delete-proof / edit-proof once it has ended. An
 * in-progress trip is still being written by the phone.
 */
export function isTripLive(trip: Pick<Trip, 'status'>): boolean {
  return trip.status === 'in_progress';
}

/** Gallons implied by distance and the vehicle's economy, for live editing. */
export function previewFuelUsed(distanceMiles: number, mpg: number): number {
  const m = num(distanceMiles);
  const economy = num(mpg);
  if (m <= 0 || economy <= 0) return 0;
  return Math.round((m / economy) * 100) / 100;
}

/** Cost implied by distance and the vehicle's fuel price, for live editing. */
export function previewCost(distanceMiles: number, mpg: number, costPerGallon: number): number {
  return Math.round(previewFuelUsed(distanceMiles, mpg) * num(costPerGallon) * 100) / 100;
}
/** How many trips the Wander page lists before "See all trips". */
export const RECENT_TRIP_COUNT = 5;

/** Newest first, by when the trip started. */
export function sortTripsNewestFirst(trips: Trip[]): Trip[] {
  return [...trips].sort((a, b) => num(b.start_time) - num(a.start_time));
}

export type TripPeriod = 'day' | 'week' | 'month' | 'year';

export interface TripGroup {
  key: string;
  label: string;
  trips: Trip[];
  stats: TripStats;
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const SHORT_MONTHS = MONTHS.map((m) => m.slice(0, 3));
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/** Weeks start on Sunday, as the calendar on the phone does. */
function startOfWeek(d: Date): Date {
  const day = startOfDay(d);
  day.setDate(day.getDate() - day.getDay());
  return day;
}

function periodStart(d: Date, period: TripPeriod): Date {
  switch (period) {
    case 'day':
      return startOfDay(d);
    case 'week':
      return startOfWeek(d);
    case 'month':
      return new Date(d.getFullYear(), d.getMonth(), 1);
    case 'year':
      return new Date(d.getFullYear(), 0, 1);
  }
}

function periodLabel(start: Date, period: TripPeriod, now: Date): string {
  const sameYear = start.getFullYear() === now.getFullYear();
  const md = `${SHORT_MONTHS[start.getMonth()]} ${start.getDate()}${sameYear ? '' : `, ${start.getFullYear()}`}`;
  switch (period) {
    case 'day': {
      const today = startOfDay(now).getTime();
      const days = Math.round((today - start.getTime()) / 86_400_000);
      if (days === 0) return 'Today';
      if (days === 1) return 'Yesterday';
      return `${WEEKDAYS[start.getDay()]}, ${md}`;
    }
    case 'week': {
      const thisWeek = startOfWeek(now).getTime();
      const weeks = Math.round((thisWeek - start.getTime()) / (7 * 86_400_000));
      if (weeks === 0) return 'This week';
      if (weeks === 1) return 'Last week';
      return `Week of ${md}`;
    }
    case 'month':
      return `${MONTHS[start.getMonth()]}${sameYear ? '' : ` ${start.getFullYear()}`}`;
    case 'year':
      return String(start.getFullYear());
  }
}

/**
 * Trips grouped by the day, week, month or year they started in (local time),
 * newest group first, each with its own totals.
 */
export function groupTrips(trips: Trip[], period: TripPeriod, now: Date = new Date()): TripGroup[] {
  const groups = new Map<number, Trip[]>();
  for (const trip of sortTripsNewestFirst(trips)) {
    const started = num(trip.start_time);
    if (!started) continue;
    const key = periodStart(new Date(started * 1000), period).getTime();
    const list = groups.get(key);
    if (list) list.push(trip);
    else groups.set(key, [trip]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => b - a)
    .map(([key, list]) => ({
      key: `${period}-${key}`,
      label: periodLabel(new Date(key), period, now),
      trips: list,
      stats: tripStats(list),
    }));
}
