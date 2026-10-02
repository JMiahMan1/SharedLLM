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
