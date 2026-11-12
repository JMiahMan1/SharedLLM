import { describe, it, expect } from 'vitest';
import { averageMinutes, dayBars, hourHeatmap, kindOf, kindTotals, limitFor, memberState, nextRoutineLine, placeAt } from './memberPage';
import type { ScreenTimeStatus, UsageDay } from '../types/api';

const TODAY = '2026-10-24'; // a Saturday
const usage = (day: string, minutes: number): UsageDay => ({ day, millis: minutes * 60000, first_use: null, last_use: null, sessions: 1 });

describe('the family member page arithmetic', () => {
  it('decides a day\'s limit the way the server does', () => {
    const rules = { daily_limit_min: 120, weekend_limit_min: 180, day_limits: { fri: 150 } };
    expect(limitFor(rules, 'mon')).toBe(120);
    expect(limitFor(rules, 'sat')).toBe(180);
    expect(limitFor(rules, 'fri')).toBe(150);
    expect(limitFor({}, 'mon')).toBeNull();
  });

  it('leaves a day with no report empty instead of calling it zero', () => {
    const bars = dayBars([usage('2026-10-24', 90), usage('2026-10-22', 150)], { daily_limit_min: 120 }, 3, TODAY);
    expect(bars.map((b) => [b.day, b.minutes, b.over])).toEqual([
      ['2026-10-22', 150, true],
      ['2026-10-23', null, false],
      ['2026-10-24', 90, false],
    ]);
    expect(averageMinutes(bars)).toBe(120); // of the two reported days
    expect(averageMinutes(dayBars([], undefined, 7, TODAY))).toBeNull();
  });

  it('names the state a child is in', () => {
    const base = { paused: false, bedtime_active: false, locked: false } as ScreenTimeStatus;
    const now = Date.now();
    expect(memberState({ ...base, paused: true, locked: true }, undefined)).toBe('paused');
    expect(memberState({ ...base, bedtime_active: true, locked: true }, undefined)).toBe('bedtime');
    expect(memberState({ ...base, locked: true }, undefined)).toBe('locked');
    expect(memberState(base, undefined)).toBe('offline');
    expect(memberState(base, { ...usage(TODAY, 5), last_use: now - 60_000 }, now)).toBe('in_use');
    expect(memberState(base, { ...usage(TODAY, 5), last_use: now - 3_600_000 }, now)).toBe('idle');
  });

  it('says when the next routine starts', () => {
    const now = Date.parse('2026-10-24T18:50:00Z');
    expect(nextRoutineLine({ next_lock_at: '2026-10-24T21:00:00Z', next_lock_reason: 'bedtime' } as ScreenTimeStatus, now)).toBe('Bedtime in 2 h 10 min');
    expect(nextRoutineLine({ next_lock_at: '2026-10-24T21:00:00Z', next_lock_reason: 'chores' } as ScreenTimeStatus, now)).toBe('Chores in 2 h 10 min');
    expect(nextRoutineLine({ next_lock_at: '2026-10-24T19:15:00Z', next_lock_reason: 'limit' } as ScreenTimeStatus, now)).toBe("Today's time runs out in 25 min");
    expect(nextRoutineLine({ next_lock_at: null } as ScreenTimeStatus, now)).toBeNull();
  });

  it('counts recorded activity by hour and day, and kinds over a window', () => {
    const at = (day: string, hour: number) => new Date(`${day}T${String(hour).padStart(2, '0')}:15:00`).toISOString();
    const events = [
      { user: 'k', feature: 'web', item: 'a', action: 'visit', at: at('2026-10-24', 20) },
      { user: 'k', feature: 'sms', item: 'b', action: 'in', at: at('2026-10-24', 20) },
      { user: 'k', feature: 'bible', item: 'c', action: 'read', at: at('2026-10-20', 7) },
      { user: 'k', feature: 'music', item: 'd', action: 'play', at: at('2026-09-01', 7) },
    ];
    const map = hourHeatmap(events, 14, TODAY);
    expect(map.days).toHaveLength(14);
    expect(map.cells.find((c) => c.day === '2026-10-24' && c.hour === 20)?.count).toBe(2);
    expect(map.max).toBe(2);
    expect(map.since).toBe('2026-09-01');
    expect(kindTotals(events, 7, TODAY)).toEqual({ web: 1, comms: 1, bible: 1 });
    expect(kindTotals(events, 30, TODAY)).toEqual({ web: 1, comms: 1, bible: 1 });
  });

  it('finds the family place a position is in', () => {
    const places = [{ id: 'z', name: 'School', lat: 39.7384, lon: -104.994, radius: 200, important: true, notify: [] }];
    expect(placeAt(39.7385, -104.9941, places)).toBe('School');
    expect(placeAt(39.79, -104.92, places)).toBeNull();
  });
});

describe('activity kinds', () => {
  it('files chat and social notifications under Social, not Other', () => {
    expect(kindOf('social')).toBe('social');
    expect(kindOf('sms')).toBe('comms');
    expect(kindOf('something-new')).toBe('other');
  });
});

import { callLength, commsLine } from './memberPage';

describe('calls and texts on the timeline', () => {
  it('a call says who, which way and how long', () => {
    expect(commsLine({ feature: 'calls', item: 'Mom', duration_s: 252, detail: { direction: 'out', number: '+15555550101' } }))
      .toEqual({ title: 'Call to Mom', number: '+15555550101', length: '4 min 12 s', text: null });
    expect(commsLine({ feature: 'calls', item: 'Mom', duration_s: 0, detail: { direction: 'missed', number: '+15555550101' } })?.title)
      .toBe('Missed call from Mom');
    // A missed call has no length to show.
    expect(commsLine({ feature: 'calls', item: 'Mom', duration_s: 0, detail: { direction: 'missed' } })?.length).toBeNull();
  });

  it('a text carries its whole message, and the number when the line names a contact', () => {
    const long = 'See you at 5. '.repeat(40);
    expect(commsLine({ feature: 'sms', item: 'Leo', detail: { direction: 'in', number: '+15555550102', body: long } }))
      .toEqual({ title: 'Text from Leo', number: '+15555550102', length: null, text: long });
    expect(commsLine({ feature: 'sms', item: '+15555550102', detail: { direction: 'out', number: '+15555550102', body: 'ok' } }))
      .toEqual({ title: 'Text to +15555550102', number: null, length: null, text: 'ok' });
  });

  it('marks chat and picture or group messages', () => {
    expect(commsLine({ feature: 'sms', item: 'Leo', detail: { direction: 'in', body: 'omw', rcs: true } })?.title).toBe('Text from Leo (chat)');
    expect(commsLine({ feature: 'sms', item: 'Mom and Leo', detail: { direction: 'out', body: 'ok', mms: true } })?.title)
      .toBe('Text to Mom and Leo (picture or group)');
  });

  it('is nothing for other kinds', () => {
    expect(commsLine({ feature: 'bible', item: 'John 3' })).toBeNull();
  });

  it('formats call lengths', () => {
    expect([callLength(42), callLength(120), callLength(3725)]).toEqual(['42 s', '2 min', '1 h 2 min']);
  });
});
