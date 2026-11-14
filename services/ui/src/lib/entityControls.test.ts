import { describe, expect, it, vi } from 'vitest';

vi.mock('./serverUrl', () => ({ getServerOrigin: () => 'https://jarvis.example.test/' }));

import {
  commandLabel, formatNumber, imageMediaUrl, numberRange, remoteCommands, segmentCleanData, selectOptions, snapNumber,
  timeInputValue, timeServiceValue, updateInfo, updateLine, vacuumSegments,
} from './entityControls';

describe('number entities', () => {
  it('reads the range, step, mode and unit the device published', () => {
    expect(numberRange({ attributes: { min: 10, max: 30, step: 0.5, mode: 'box', unit_of_measurement: '°C' }, unit: null }))
      .toEqual({ min: 10, max: 30, step: 0.5, mode: 'box', unit: '°C' });
  });

  it('falls back to HA\'s defaults and picks a slider for a small range', () => {
    expect(numberRange({ attributes: {}, unit: '%' })).toEqual({ min: 0, max: 100, step: 1, mode: 'slider', unit: '%' });
    expect(numberRange({ attributes: { min: 0, max: 65534, step: 1, mode: 'auto' }, unit: null }).mode).toBe('box');
    expect(numberRange({ attributes: { min: 5, max: 1, step: 0 }, unit: null })).toMatchObject({ min: 1, max: 5, step: 1 });
  });

  it('snaps a value onto the grid and inside the range', () => {
    const r = numberRange({ attributes: { min: 0, max: 1, step: 0.1 }, unit: null });
    expect(snapNumber(0.3000001, r)).toBe(0.3);
    expect(snapNumber(5, r)).toBe(1);
    expect(snapNumber(-2, r)).toBe(0);
    expect(formatNumber(0.3, r)).toBe('0.3');
    expect(formatNumber(null, r)).toBe('--');
    expect(formatNumber(40, numberRange({ attributes: {}, unit: '%' }))).toBe('40 %');
  });
});

describe('selects, times and remotes', () => {
  it('lists a select\'s options', () => {
    expect(selectOptions({ attributes: { options: ['low', 'high'] } })).toEqual(['low', 'high']);
    expect(selectOptions({ attributes: {} })).toEqual([]);
  });

  it('turns a time state into an input value and back', () => {
    expect(timeInputValue('22:00:00')).toBe('22:00');
    expect(timeInputValue('7:05')).toBe('07:05');
    expect(timeInputValue('unknown')).toBe('');
    expect(timeInputValue('25:00')).toBe('');
    expect(timeServiceValue('21:15')).toBe('21:15:00');
    expect(timeServiceValue('')).toBeNull();
  });

  it('offers the commands and activities a remote publishes', () => {
    expect(remoteCommands({ attributes: { command_list: ['home', 'up'], activity_list: ['Movies'] } }))
      .toEqual({ commands: ['home', 'up'], activities: ['Movies'] });
    expect(remoteCommands({ attributes: {} })).toEqual({ commands: [], activities: [] });
    expect(commandLabel('volume_up')).toBe('Volume up');
  });
});

describe('update entities', () => {
  const upd = (state: string, attributes: Record<string, unknown>, abilities?: string[]) =>
    updateInfo({ state, attributes, abilities });

  it('says what is installed, what is offered and whether it can be installed here', () => {
    const u = upd('on', { installed_version: '1.0.0', latest_version: '2.0.0', release_url: 'https://example.com/notes',
      supported_features: 1 }, ['firmware', 'update']);
    expect(u).toMatchObject({ installed: '1.0.0', latest: '2.0.0', available: true, canInstall: true,
      releaseUrl: 'https://example.com/notes' });
    expect(updateLine(u)).toBe('2.0.0 available (1.0.0 installed)');
  });

  it('never offers install the hub cannot do, and never links a non-web release URL', () => {
    const u = upd('on', { installed_version: '1', latest_version: '2', supported_features: 23,
      release_url: 'javascript:alert(1)' }, ['firmware']);
    expect(u.canInstall).toBe(false);
    expect(u.releaseUrl).toBeNull();
    expect(upd('unavailable', { supported_features: 1 }).canInstall).toBe(false);
    expect(upd('on', { supported_features: 1 }).canInstall).toBe(true); // an older hub without abilities
  });

  it('words every case', () => {
    expect(updateLine(upd('off', { installed_version: '1', latest_version: '1' }))).toBe('Up to date (1)');
    expect(updateLine(upd('off', { installed_version: '1', latest_version: '2', skipped_version: '2' })))
      .toBe('2 skipped (1 installed)');
    expect(updateLine(upd('unknown', { installed_version: '1' }))).toBe('1 installed; latest not known');
    expect(updateLine(upd('on', { in_progress: true }))).toBe('Installing...');
    expect(updateLine(upd('unknown', {}))).toBe('Version not known');
  });
});

describe('vacuum rooms and image pictures', () => {
  it('reads the rooms a vacuum published and builds the clean call', () => {
    expect(vacuumSegments({ attributes: { segments: [{ id: 16, name: 'Kitchen' }, { id: '17' }, { name: 'x' }] } }))
      .toEqual([{ id: 16, name: 'Kitchen' }, { id: 17, name: 'Room 17' }]);
    expect(vacuumSegments({ attributes: {} })).toEqual([]);
    expect(segmentCleanData([16, 17])).toEqual({ command: 'app_segment_clean', params: [{ segments: [16, 17], repeat: 1 }] });
  });

  it('signs the picture URL with the media token and changes it when the picture does', () => {
    const token = { token: 'mt-1', user: 'robin', expiresAt: 0 };
    expect(imageMediaUrl(null, 'image.map')).toBeNull();
    expect(imageMediaUrl(token, 'image.map', '2026-11-14T10:00:00+00:00'))
      .toBe('https://jarvis.example.test/api/media/images/image.map?user=robin&mt=mt-1&v=2026-11-14T10%3A00%3A00%2B00%3A00');
    expect(imageMediaUrl(token, 'image.map', 'unknown')).toBe('https://jarvis.example.test/api/media/images/image.map?user=robin&mt=mt-1');
  });
});
