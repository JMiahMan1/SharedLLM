import { describe, it, expect } from 'vitest';
import {
  STATIONARY_HEARTBEAT_MS,
  isInsideGeofence,
  shouldUploadFix,
} from '../lib/locationSync';

const base = { insideGeofence: false, moving: true, lastUploadAt: null, now: 1_000_000 };

describe('shouldUploadFix', () => {
  it('uploads the first fix of the session', () => {
    expect(shouldUploadFix({ ...base, moving: false, insideGeofence: true })).toBe(true);
  });

  it('uploads whenever the person is moving', () => {
    expect(
      shouldUploadFix({
        ...base,
        moving: true,
        insideGeofence: true,
        lastUploadAt: 1_000_000,
        now: 1_000_001,
      }),
    ).toBe(true);
  });

  it('uploads a real move out of the geofence even while stationary', () => {
    expect(
      shouldUploadFix({ ...base, moving: false, insideGeofence: false, lastUploadAt: 1_000_000 }),
    ).toBe(true);
  });

  // The regression: a stationary person inside the geofence used to never be
  // uploaded at all, so the map showed their position going stale while they
  // sat at home.
  it('throttles a stationary person inside the geofence', () => {
    expect(
      shouldUploadFix({
        insideGeofence: true,
        moving: false,
        lastUploadAt: 1_000_000,
        now: 1_000_000 + 1_000,
      }),
    ).toBe(false);
  });

  it('refreshes a stationary person once the heartbeat elapses', () => {
    // This is the fix: "still here" becomes a timestamped fact rather than an
    // absence, so the map stops ageing a person who has not moved.
    expect(
      shouldUploadFix({
        insideGeofence: true,
        moving: false,
        lastUploadAt: 1_000_000,
        now: 1_000_000 + STATIONARY_HEARTBEAT_MS,
      }),
    ).toBe(true);
  });

  it('uploads again after the heartbeat even if many fixes arrived', () => {
    expect(
      shouldUploadFix({
        insideGeofence: true,
        moving: false,
        lastUploadAt: 0,
        now: STATIONARY_HEARTBEAT_MS * 10,
      }),
    ).toBe(true);
  });

  it('starts moving without waiting out a heartbeat window', () => {
    // Someone who sat still for an hour and then walks must be uploaded at
    // once, not up to five minutes later.
    expect(
      shouldUploadFix({
        insideGeofence: true,
        moving: true,
        lastUploadAt: 1_000_000,
        now: 1_000_000 + 10,
      }),
    ).toBe(true);
  });

  it('honours a custom heartbeat', () => {
    expect(
      shouldUploadFix({
        insideGeofence: true,
        moving: false,
        lastUploadAt: 0,
        now: 30_000,
        heartbeatMs: 10_000,
      }),
    ).toBe(true);
  });
});

describe('isInsideGeofence', () => {
  const flat = () => 0;

  it('is false with no previous fix', () => {
    expect(isInsideGeofence(null, 33.4, -112.0, flat)).toBe(false);
  });

  it('is true when the movement is under the radius', () => {
    expect(isInsideGeofence({ lat: 33.4, lng: -112.0 }, 33.4001, -112.0, () => 50)).toBe(true);
  });

  it('is false once the movement exceeds the radius', () => {
    expect(isInsideGeofence({ lat: 33.4, lng: -112.0 }, 33.4, -112.0, () => 500)).toBe(false);
  });

  it('treats exactly the radius as outside', () => {
    expect(isInsideGeofence({ lat: 0, lng: 0 }, 0, 0, () => 200)).toBe(false);
  });
});

// Production, 2026-10-06: a drive's fixes arrived in bursts, GPS (7-24 m)
// interleaved with a network cluster 800 m away (120-600 m), all sent at one
// time. Lines were drawn off the road and a 10 s "trip" topped 95 mph.
import { classifyFix, derivedSpeedMps } from '../lib/locationSync';

describe('classifyFix', () => {
  const base = { lastUploadedFixTs: 1_000_000, lastGoodFixTs: 1_000_000 };
  it('drops a fix older than the last one sent', () => {
    expect(classifyFix({ ...base, fixTs: 999_000, accuracy: 5 })).toBe('stale');
  });
  it('drops a network fix while GPS fixes are arriving', () => {
    expect(classifyFix({ ...base, fixTs: 1_010_000, accuracy: 142 })).toBe('coarse');
  });
  it('keeps a coarse fix when it is all there is (indoors)', () => {
    expect(classifyFix({ ...base, fixTs: 1_000_000 + 5 * 60_000, accuracy: 142 })).toBe('ok');
  });
  it('keeps a good fix', () => {
    expect(classifyFix({ ...base, fixTs: 1_010_000, accuracy: 8 })).toBe('ok');
  });
});

describe('derivedSpeedMps', () => {
  const metres = (aLat: number, _aLng: number, bLat: number) => Math.abs(bLat - aLat);  // 1 unit = 1 m
  it('measures between two GPS fixes a few seconds apart', () => {
    expect(derivedSpeedMps({ lat: 0, lng: 0, t: 0, acc: 8 }, { lat: 100, lng: 0, t: 10_000, acc: 6 }, metres)).toBe(10);
  });
  it('ignores fixes milliseconds apart', () => {
    expect(derivedSpeedMps({ lat: 0, lng: 0, t: 0, acc: 8 }, { lat: 800, lng: 0, t: 5, acc: 6 }, metres)).toBe(0);
  });
  it('ignores a network fix', () => {
    expect(derivedSpeedMps({ lat: 0, lng: 0, t: 0, acc: 8 }, { lat: 800, lng: 0, t: 10_000, acc: 142 }, metres)).toBe(0);
  });
  it('ignores an impossible speed', () => {
    expect(derivedSpeedMps({ lat: 0, lng: 0, t: 0, acc: 8 }, { lat: 900, lng: 0, t: 5_000, acc: 6 }, metres)).toBe(0);
  });
});
