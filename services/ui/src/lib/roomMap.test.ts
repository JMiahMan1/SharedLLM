import { describe, it, expect, vi } from 'vitest';
import { AxiosError, AxiosHeaders } from 'axios';
import {
  beaconTrackerId, clock, confusedPairs, percent, phoneTrackers, roomRows, startErrorText, validTagId, STALE_AFTER_DAYS,
} from './roomMap';
import type { Phone } from './phones';

vi.mock('../services/api', () => ({ apiClient: {} }));

const UUID = '00000000-0000-4000-8000-0000000000aa';
const NOW = Date.parse('2026-11-12T12:00:00Z');

const axiosErr = (status: number, data: unknown) => new AxiosError('x', 'ERR', undefined, undefined, {
  status, statusText: '', headers: {}, config: { headers: new AxiosHeaders() }, data,
});

describe('roomMap', () => {
  it('names this phone the way the hub names beacons', () => {
    expect(beaconTrackerId({ uuid: UUID, major: 1, minor: 7 })).toBe(`${UUID}-1-7`);
    expect(validTagId(`${UUID}-1-7`)).toBe(true);
    expect(validTagId('den-tag')).toBe(false);
  });

  it('follows a phone by its room sensor, and says why a phone without one cannot be mapped', () => {
    const phone = (entry_id: string, name: string, sensors: string[]) => ({
      entry_id, name, sensors: sensors.map((entity_id) => ({ entity_id, domain: 'sensor' })),
    }) as unknown as Phone;
    expect(phoneTrackers([
      phone('p1', "Alex's Phone", ['sensor.alex_s_phone_room', 'sensor.alex_s_phone_room_distance']),
      phone('p2', "Sam's Phone", ['sensor.sam_s_phone_battery_level']),
    ])).toEqual([
      { id: 'sensor.alex_s_phone', label: "Alex's Phone" },
      { id: 'phone:p2', label: "Sam's Phone", unavailable: 'no beacon registered with the hub' },
    ]);
  });

  it('lists every hub room as not mapped, mapped or stale', () => {
    const old = new Date(NOW - (STALE_AFTER_DAYS + 1) * 86_400_000).toISOString();
    const map = {
      method: 'fingerprint' as const, scanners: [], confusion: {}, overall_accuracy: 0.8,
      areas: [
        { area_id: 'den', name: 'Den', samples: 120, sessions: 2, last_recorded: '2026-11-10T12:00:00Z', accuracy: 0.9 },
        { area_id: 'loft', name: 'Loft', samples: 40, sessions: 1, last_recorded: old, accuracy: 0.6 },
      ],
    };
    const rows = roomRows([{ area_id: 'den', name: 'Den' }, { area_id: 'loft', name: 'Loft' }, { area_id: 'hall', name: 'Hall' }], map, NOW);
    expect(rows.map((r) => r.state)).toEqual(['mapped', 'stale', 'not_mapped']);
    expect(rows[1].staleWhy).toContain(`${STALE_AFTER_DAYS} days`);
    expect(roomRows([{ area_id: 'den', name: 'Den' }], { ...map, stale_reason: 'A listener moved' }, NOW)[0])
      .toMatchObject({ state: 'stale', staleWhy: 'A listener moved' });
  });

  it('finds the rooms mixed up most, both directions added, worst first', () => {
    expect(confusedPairs({ den: { den: 50, loft: 4, hall: 1 }, loft: { den: 3, loft: 40 }, hall: { hall: 9 } },
      { den: 'Den', loft: 'Loft', hall: 'Hall' })).toEqual([
      { a: 'Den', b: 'Loft', count: 7 },
      { a: 'Den', b: 'Hall', count: 1 },
    ]);
    expect(confusedPairs(undefined, {})).toEqual([]);
  });

  it('says plainly why a recording could not start', () => {
    expect(startErrorText(axiosErr(409, { detail: 'not heard in the last 30 s' }), true))
      .toBe("Your phone isn't being heard. Check that Room detection is on in the Jarvis app. (not heard in the last 30 s)");
    expect(startErrorText(axiosErr(409, {}), false)).toContain("That tracker isn't being heard");
    expect(startErrorText(axiosErr(404, { detail: 'No such area' }), true)).toBe('No such area');
    expect(startErrorText(new Error('x'), true)).toBe('The recording could not start. Try again.');
  });

  it('formats accuracy and the countdown', () => {
    expect(percent(0.824)).toBe('82%');
    expect(percent(91)).toBe('91%');
    expect(percent(null)).toBe('not measured');
    expect(clock(65)).toBe('1:05');
    expect(clock(-3)).toBe('0:00');
  });
});
