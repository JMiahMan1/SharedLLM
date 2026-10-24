import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  formatDuration, formatRemaining, formatTimeAgo, formatTimeLeft, normalizePlayedAt, spokenDuration, UNKNOWN_TIME,
} from '../format';
import { mediaDebugEnabled, mediaLog } from '../log';

describe('formatDuration', () => {
  it.each([
    [0, '0:00'],
    [59, '0:59'],
    [60, '1:00'],
    [3599, '59:59'],
    [3600, '1:00:00'],
    [36012, '10:00:12'],
    ['125', '2:05'],
  ])('BUG-41 formats %s as %s', (input, out) => {
    expect(formatDuration(input)).toBe(out);
  });

  it.each([NaN, undefined, null, '', -5, 'abc', Infinity])('UX-36 shows --:-- for unknown %s', (input) => {
    expect(formatDuration(input)).toBe(UNKNOWN_TIME);
  });
});

describe('formatRemaining', () => {
  it('counts down what is left', () => {
    expect(formatRemaining(30, 222)).toBe('-3:12');
    expect(formatRemaining(300, 222)).toBe('-0:00');
    expect(formatRemaining(undefined, 60)).toBe('-1:00');
  });
  it('is unknown without a length', () => {
    expect(formatRemaining(10, 0)).toBe(UNKNOWN_TIME);
    expect(formatRemaining(10, null)).toBe(UNKNOWN_TIME);
  });
});

describe('formatTimeLeft', () => {
  it('reads hours and minutes', () => {
    expect(formatTimeLeft(0, 2 * 3600 + 13 * 60 + 10)).toBe('2h 13m left');
    expect(formatTimeLeft(60, 14 * 60)).toBe('13m left');
    expect(formatTimeLeft(50, 60)).toBe('Under a minute left');
    expect(formatTimeLeft(0, 0)).toBeNull();
  });
});

describe('spokenDuration', () => {
  it('spells the time out', () => {
    expect(spokenDuration(72)).toBe('1 minute 12 seconds');
    expect(spokenDuration(3725)).toBe('1 hour 2 minutes 5 seconds');
    expect(spokenDuration(0)).toBe('0 seconds');
    expect(spokenDuration(7200)).toBe('2 hours');
    expect(spokenDuration(undefined)).toBe('unknown');
  });
});

describe('normalizePlayedAt', () => {
  it('accepts seconds, milliseconds and ISO strings', () => {
    expect(normalizePlayedAt(1_700_000_000)).toBe(1_700_000_000_000);
    expect(normalizePlayedAt(1_700_000_000_000)).toBe(1_700_000_000_000);
    expect(normalizePlayedAt('2026-10-24T08:00:00Z')).toBe(Date.parse('2026-10-24T08:00:00Z'));
  });
  it('drops empty and invalid values', () => {
    expect(normalizePlayedAt(undefined)).toBeUndefined();
    expect(normalizePlayedAt('')).toBeUndefined();
    expect(normalizePlayedAt(0)).toBeUndefined();
    expect(normalizePlayedAt('not a date')).toBeUndefined();
  });
});

describe('formatTimeAgo', () => {
  const now = Date.parse('2026-10-24T12:00:00Z');
  it('buckets by age', () => {
    expect(formatTimeAgo(now - 10_000, now)).toBe('Just now');
    expect(formatTimeAgo(now - 5 * 60_000, now)).toBe('5m ago');
    expect(formatTimeAgo(now - 3 * 3600_000, now)).toBe('3h ago');
    expect(formatTimeAgo(now - 2 * 86400_000, now)).toBe('2d ago');
    expect(formatTimeAgo(now - 30 * 86400_000, now)).toMatch(/Sep \d+/);
  });
});

describe('mediaLog', () => {
  afterEach(() => {
    localStorage.removeItem('jarvis_debug_media');
    vi.restoreAllMocks();
  });
  it('BUG-70 stays silent unless debugging is on', () => {
    const spy = vi.spyOn(console, 'debug').mockImplementation(() => {});
    mediaLog('tick');
    expect(spy).not.toHaveBeenCalled();
    localStorage.setItem('jarvis_debug_media', '1');
    expect(mediaDebugEnabled()).toBe(true);
    mediaLog('tick');
    expect(spy).toHaveBeenCalledWith('[media]', 'tick');
  });
});
