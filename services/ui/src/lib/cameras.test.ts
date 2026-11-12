import { describe, it, expect } from 'vitest';
import {
  addDays, cameraMediaUrl, dayRange, formatDuration, fromDateInput, tileRefreshMs, toDateInput, triggerLabel,
  type CameraInfo,
} from './cameras';

const TOKEN = { token: 'mt-1', user: 'alice', expiresAt: 4_000_000_000 };

describe('cameras helpers', () => {
  it('signs media URLs with the media token and escapes the file', () => {
    expect(cameraMediaUrl(null, 'camera.x', 'snapshot')).toBeNull();
    const url = new URL(cameraMediaUrl(TOKEN, 'camera.x', 'play', { file: 'Mp4Record/a b&c.mp4' })!);
    expect(url.pathname).toBe('/api/media/cameras/camera.x/recordings/play');
    expect(url.searchParams.get('file')).toBe('Mp4Record/a b&c.mp4');
    expect(url.searchParams.get('mt')).toBe('mt-1');
    expect(url.searchParams.get('user')).toBe('alice');
  });

  it('covers a whole past day, and today only up to now', () => {
    const now = new Date(2026, 10, 11, 15, 30);
    const past = dayRange(new Date(2026, 10, 9, 12), now);
    expect(new Date(past.start)).toEqual(new Date(2026, 10, 9));
    expect(new Date(past.end)).toEqual(new Date(2026, 10, 9, 23, 59, 59, 999));
    const today = dayRange(new Date(2026, 10, 11, 8), now);
    expect(new Date(today.end)).toEqual(now);
  });

  it('round-trips the date input and steps days across a month', () => {
    expect(toDateInput(new Date(2026, 0, 5))).toBe('2026-01-05');
    expect(fromDateInput('2026-01-05')).toEqual(new Date(2026, 0, 5));
    expect(fromDateInput('nope')).toBeNull();
    expect(addDays(new Date(2026, 10, 1), -1)).toEqual(new Date(2026, 9, 31));
  });

  it('formats durations and trigger names', () => {
    expect(formatDuration(30)).toBe('0:30');
    expect(formatDuration(185)).toBe('3:05');
    expect(formatDuration(3727)).toBe('1:02:07');
    expect(triggerLabel('person')).toBe('Person');
    expect(triggerLabel('forgotten_item')).toBe('Item left');
    expect(triggerLabel('something_new')).toBe('Something new');
  });

  it('never refreshes a battery camera tile on its own', () => {
    const cam = { entity_id: 'camera.x', name: 'X', platform: 'reolink', state: 'idle', recordings: true, live: [] } as CameraInfo;
    expect(tileRefreshMs({ ...cam, battery: true })).toBeNull();
    expect(tileRefreshMs(cam)).toBe(10_000);
  });
});
