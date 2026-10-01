import { describe, expect, it } from 'vitest';
import { daysBetween, hasDayRolledOver, localDayKey } from './stepDay';

describe('localDayKey', () => {
  it('formats the local calendar day as YYYY-MM-DD', () => {
    expect(localDayKey(new Date(2026, 9, 1))).toBe('2026-10-01');
  });

  it('zero-pads single-digit months and days so keys sort lexically', () => {
    expect(localDayKey(new Date(2026, 0, 5))).toBe('2026-01-05');
    expect(localDayKey(new Date(2026, 11, 25))).toBe('2026-12-25');
  });

  it('uses local time, so a late-evening reading stays on the same day', () => {
    expect(localDayKey(new Date(2026, 9, 1, 23, 59, 59))).toBe('2026-10-01');
    expect(localDayKey(new Date(2026, 9, 2, 0, 0, 1))).toBe('2026-10-02');
  });
});

describe('hasDayRolledOver', () => {
  it('is false within the same local day', () => {
    const now = new Date(2026, 9, 1, 22, 0, 0);
    expect(hasDayRolledOver('2026-10-01', now)).toBe(false);
  });

  it('is true the moment the local day changes', () => {
    expect(hasDayRolledOver('2026-09-30', new Date(2026, 9, 1, 0, 0, 1))).toBe(true);
  });

  it('detects a rollover even when the app was closed across midnight', () => {
    // The old check only looked at a 60 s window; this fires whenever the next
    // reading is taken, regardless of how long the gap was.
    expect(hasDayRolledOver('2026-09-20', new Date(2026, 9, 1, 9, 30))).toBe(true);
  });

  it('treats a missing previous key as a rollover so a cold start re-reads', () => {
    expect(hasDayRolledOver(null, new Date(2026, 9, 1))).toBe(true);
  });

  it('is not fooled by two dates that merely share a prefix', () => {
    expect(hasDayRolledOver('2026-09-01', new Date(2026, 9, 1))).toBe(true);
  });
});

describe('daysBetween', () => {
  it('counts whole local days', () => {
    expect(daysBetween('2026-09-30', '2026-10-01')).toBe(1);
    expect(daysBetween('2026-09-25', '2026-10-01')).toBe(6);
    expect(daysBetween('2026-10-01', '2026-10-01')).toBe(0);
  });

  it('survives a month and year boundary', () => {
    expect(daysBetween('2026-02-28', '2026-03-01')).toBe(1);
    expect(daysBetween('2025-12-31', '2026-01-01')).toBe(1);
  });

  it('returns null rather than NaN for an unusable key', () => {
    expect(daysBetween('not-a-day', '2026-10-01')).toBeNull();
    expect(daysBetween('2026-10-01', '')).toBeNull();
  });
});