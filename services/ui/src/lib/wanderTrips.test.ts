import { describe, expect, it } from 'vitest';
import {
  displayName,
  filterTrips,
  formatDistanceMeters,
  formatMiles,
  formatMoney,
  formatTripLocation,
  isTripLive,
  isTripOwner,
  previewCost,
  previewFuelUsed,
  relativeTime,
  tripLocationCoords,
  tripOwner,
  formatCostSummary,
  formatFuelSummary,
  roundForDisplay,
  tripStats,
} from './wanderTrips';
import type { Trip } from '../types/api';

const trip = (over: Partial<Trip> = {}): Trip => ({
  id: 't1',
  user_id: 'person.jeremiah',
  user_name: 'Jeremiah',
  mpg: 25,
  cost_per_gallon: 3.65,
  start_time: 0,
  end_time: 100,
  duration_seconds: 100,
  distance_miles: 0,
  top_speed_mph: 0,
  fuel_used_gal: 0,
  trip_cost_usd: 0,
  activity_type: 'driving',
  status: 'completed',
  ...over,
});

describe('tripOwner', () => {
  it('strips the geo entity prefix', () => {
    expect(tripOwner({ user_id: 'person.jeremiah' })).toBe('jeremiah');
  });

  it('lower-cases', () => {
    expect(tripOwner({ user_id: 'PERSON.Michele' })).toBe('michele');
  });

  it('is empty for a missing owner', () => {
    expect(tripOwner({ user_id: '' })).toBe('');
  });
});

describe('isTripOwner', () => {
  it('matches the owner through the entity prefix', () => {
    expect(isTripOwner(trip(), 'jeremiah')).toBe(true);
  });

  it('ignores case on both sides', () => {
    expect(isTripOwner(trip({ user_id: 'person.Michele' }), 'michele')).toBe(true);
  });

  it('rejects a different user', () => {
    expect(isTripOwner(trip(), 'michele')).toBe(false);
  });

  it('denies when nobody is signed in', () => {
    expect(isTripOwner(trip(), undefined)).toBe(false);
    expect(isTripOwner(trip(), '')).toBe(false);
  });

  it('does not grant ownership to an admin who is not the owner', () => {
    // Geo's update_trip has no admin bypass, so pretending otherwise produced a
    // dead UI affordance that always 403'd.
    expect(isTripOwner(trip({ user_id: 'person.michele' }), 'jeremiah')).toBe(false);
  });

  it('no longer has a magic username named admin', () => {
    // The real admin is `jeremiah`; there is no account called `admin`.
    expect(isTripOwner(trip({ user_id: 'person.michele' }), 'admin')).toBe(false);
  });

  it('still lets a genuine owner called admin edit their own trip', () => {
    expect(isTripOwner(trip({ user_id: 'person.admin' }), 'admin')).toBe(true);
  });
});

describe('displayName', () => {
  it('prefers the server-supplied name', () => {
    expect(displayName(trip())).toBe('Jeremiah');
  });

  it('derives a name when the server sent none', () => {
    expect(displayName({ user_id: 'person.michele', user_name: '' })).toBe('Michele');
  });

  it('never renders an empty label', () => {
    expect(displayName({ user_id: '', user_name: '' })).toBe('Someone');
  });
});

describe('filterTrips', () => {
  const mine = trip({ id: 'a', user_id: 'person.jeremiah' });
  const theirs = trip({ id: 'b', user_id: 'person.michele' });

  it('returns everything on the all tab', () => {
    expect(filterTrips([mine, theirs], 'all', 'michele')).toHaveLength(2);
  });

  it('returns only the caller’s own trips on the mine tab', () => {
    const filtered = filterTrips([mine, theirs], 'mine', 'michele');
    expect(filtered.map((t) => t.id)).toEqual(['b']);
  });

  it('is empty rather than everything when nobody is signed in', () => {
    expect(filterTrips([mine, theirs], 'mine', undefined)).toEqual([]);
  });
});

describe('summary formatting', () => {
  it('kills binary float noise from summed floats', () => {
    // 0.1 + 0.2 is the canonical case; these arrive from per-trip sums.
    const stats = tripStats([
      trip({ fuel_used_gal: 0.1, trip_cost_usd: 0.1 }),
      trip({ fuel_used_gal: 0.2, trip_cost_usd: 0.2 }),
    ]);
    expect(formatFuelSummary(stats.fuel)).toBe('0.3 gal');
    expect(formatCostSummary(stats.cost)).toBe('$0.3');
  });

  it('shows no decimal when the value is whole', () => {
    expect(formatFuelSummary(3)).toBe('3 gal');
    expect(formatCostSummary(12)).toBe('$12');
  });

  it('never renders a long float tail', () => {
    expect(formatFuelSummary(2.6999999999999997)).toBe('2.7 gal');
    expect(formatCostSummary(12.300000000000001)).toBe('$12.3');
  });

  it('keeps one decimal for values that need it', () => {
    expect(formatFuelSummary(0.25)).toBe('0.3 gal');
    expect(formatCostSummary(0.25)).toBe('$0.3');
  });

  it('survives missing and non-finite values', () => {
    expect(formatFuelSummary(NaN)).toBe('0 gal');
    expect(formatCostSummary(Infinity)).toBe('$0');
    expect(roundForDisplay(NaN)).toBe(0);
  });

  it('leaves the underlying totals exact', () => {
    // Rounding is a display concern only; the sum itself must stay lossless.
    const stats = tripStats([trip({ fuel_used_gal: 0.1 }), trip({ fuel_used_gal: 0.2 })]);
    expect(stats.fuel).toBeCloseTo(0.30000000000000004);
  });
});

describe('tripStats', () => {
  it('sums distance, fuel, cost and count', () => {
    const stats = tripStats([
      trip({ distance_miles: 10.5, fuel_used_gal: 0.4, trip_cost_usd: 1.5 }),
      trip({ distance_miles: 4.5, fuel_used_gal: 0.2, trip_cost_usd: 0.7 }),
    ]);
    expect(stats.miles).toBe(15);
    expect(stats.fuel).toBeCloseTo(0.6);
    expect(stats.cost).toBeCloseTo(2.2);
    expect(stats.count).toBe(2);
  });

  it('counts fuel and cost for driving trips only', () => {
    // A walking trip has no fuel economy; counting it would invent a cost.
    const stats = tripStats([
      trip({ activity_type: 'driving', distance_miles: 10, fuel_used_gal: 0.4, trip_cost_usd: 1.5 }),
      trip({ activity_type: 'walking', distance_miles: 2, fuel_used_gal: 9, trip_cost_usd: 99 }),
    ]);
    expect(stats.miles).toBe(12);
    expect(stats.fuel).toBeCloseTo(0.4);
    expect(stats.cost).toBeCloseTo(1.5);
  });

  it('treats a missing activity_type as driving', () => {
    const stats = tripStats([trip({ activity_type: '', fuel_used_gal: 0.5, trip_cost_usd: 2 })]);
    expect(stats.cost).toBeCloseTo(2);
  });

  it('counts shared trips', () => {
    expect(tripStats([trip({ is_shared: true }), trip()]).sharedCount).toBe(1);
  });

  it('handles no trips', () => {
    expect(tripStats([])).toMatchObject({ miles: 0, cost: 0, count: 0, sharedCount: 0 });
  });

  it('ignores non-numeric junk rather than producing NaN', () => {
    const stats = tripStats([
      trip({ distance_miles: Number.NaN, fuel_used_gal: Number.NaN, trip_cost_usd: Number.NaN }),
    ]);
    expect(stats.miles).toBe(0);
    expect(Number.isFinite(stats.cost)).toBe(true);
  });
});

describe('formatTripLocation', () => {
  it('prefers the resolved place name', () => {
    expect(formatTripLocation({ name: 'Home' }, 'Start')).toBe('Home');
  });

  it('falls back to coordinates', () => {
    expect(formatTripLocation({ latitude: 33.16, longitude: -111.56 }, 'Start')).toBe(
      '33.1600, -111.5600',
    );
  });

  it('accepts the legacy lat/lon spelling', () => {
    expect(formatTripLocation({ lat: 33.16, lon: -111.56 }, 'Start')).toBe('33.1600, -111.5600');
  });

  it('falls back to the zone, then the generic label', () => {
    expect(formatTripLocation({ zone: 'work' }, 'Start')).toBe('work');
    expect(formatTripLocation(undefined, 'Start')).toBe('Start');
  });

  it('rejects non-finite coordinates instead of printing NaN', () => {
    expect(formatTripLocation({ latitude: Number.NaN, longitude: 1 }, 'Start')).toBe('Start');
  });
});

describe('tripLocationCoords', () => {
  it('reads either spelling', () => {
    expect(tripLocationCoords({ latitude: 1, longitude: 2 })).toEqual({ lat: 1, lon: 2 });
    expect(tripLocationCoords({ lat: 1, lon: 2 })).toEqual({ lat: 1, lon: 2 });
  });

  it('is null when absent or unusable', () => {
    expect(tripLocationCoords(undefined)).toBeNull();
    expect(tripLocationCoords({})).toBeNull();
    expect(tripLocationCoords({ latitude: Number.NaN, longitude: 2 })).toBeNull();
  });
});

describe('relativeTime', () => {
  const now = Date.parse('2026-10-01T12:00:00Z');
  const ago = (mins: number) => new Date(now - mins * 60000).toISOString();

  it('reads as minutes, hours and days', () => {
    expect(relativeTime(ago(0), now)).toBe('just now');
    expect(relativeTime(ago(5), now)).toBe('5 min ago');
    expect(relativeTime(ago(60), now)).toBe('1 hr ago');
    expect(relativeTime(ago(300), now)).toBe('5 hrs ago');
    expect(relativeTime(ago(60 * 25), now)).toBe('yesterday');
    expect(relativeTime(ago(60 * 24 * 4), now)).toBe('4 days ago');
  });

  it('accepts epoch seconds as well as ISO', () => {
    expect(relativeTime(now / 1000 - 120, now)).toBe('2 min ago');
  });

  it('is empty for an unparseable value', () => {
    expect(relativeTime('nonsense', now)).toBe('');
    expect(relativeTime(undefined, now)).toBe('');
  });
});

describe('formatDistanceMeters', () => {
  it('switches to miles past a mile', () => {
    expect(formatDistanceMeters(120)).toBe(' · 120 m');
    expect(formatDistanceMeters(1609.34)).toBe(' · 1.0 mi');
  });

  it('is empty when unknown', () => {
    expect(formatDistanceMeters(undefined)).toBe('');
    expect(formatDistanceMeters(Number.NaN)).toBe('');
  });
});

describe('formatters', () => {
  it('formats miles and money', () => {
    expect(formatMiles(2.34)).toBe('2.3');
    expect(formatMiles(42.6)).toBe('43');
    expect(formatMoney(1.5)).toBe('$1.50');
  });
});

describe('isTripLive', () => {
  it('is true only for an in-progress trip', () => {
    expect(isTripLive({ status: 'in_progress' })).toBe(true);
    expect(isTripLive({ status: 'completed' })).toBe(false);
  });
});

describe('fuel and cost previews', () => {
  it('divides distance by economy', () => {
    expect(previewFuelUsed(25, 25)).toBe(1);
  });

  it('multiplies by price per gallon', () => {
    expect(previewCost(25, 25, 3.65)).toBe(3.65);
  });

  it('is zero rather than Infinity for a nonsense economy', () => {
    expect(previewFuelUsed(25, 0)).toBe(0);
    expect(previewCost(25, 0, 3.65)).toBe(0);
    expect(previewFuelUsed(0, 25)).toBe(0);
  });
});