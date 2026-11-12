import { describe, expect, it } from 'vitest';
import {
  daySegments, expandWeekdays, formatSlotStart, holdUntilOptions, holdUntilValue, isPowerSensor, normaliseSlots,
  parseSlotStart, parseTime, programCovers, toggleWeekday, weekdaysText, weekPrograms,
  type Program, type Thermostat,
} from './schedules';

const thermostat = (over: Partial<Thermostat> = {}): Thermostat => ({
  entity_id: 'climate.hall', name: 'Hall', state: 'heat',
  capabilities: { hvac_modes: ['off', 'heat'], fan_modes: [] },
  current: null, next: null, hold: null, effective: { source: 'schedule', setpoint: null }, summary: '',
  ...over,
});

const program = (over: Partial<Program> = {}): Program => ({
  id: 'p1', name: 'Week', entity_ids: ['climate.hall'], weekdays: ['workday'], start_date: null, end_date: null,
  enabled: true, slots: [{ start: '06:00:00', hvac_mode: 'heat', temperature: 20 }], ...over,
});

describe('weekdays', () => {
  it('expands groups', () => {
    expect([...expandWeekdays(['daily'])]).toHaveLength(7);
    expect([...expandWeekdays(['workday'])]).toEqual(['mon', 'tue', 'wed', 'thu', 'fri']);
    expect([...expandWeekdays(['weekend', 'wed'])].sort()).toEqual(['sat', 'sun', 'wed']);
    expect(expandWeekdays(['junk']).size).toBe(0);
  });
  it('names a list and toggles a day out of a group', () => {
    expect(weekdaysText(['mon', 'tue', 'wed', 'thu', 'fri'])).toBe('Workdays');
    expect(weekdaysText(['mon', 'fri'])).toBe('Mon, Fri');
    expect(toggleWeekday(['workday'], 'wed')).toEqual(['mon', 'tue', 'thu', 'fri']);
    expect(toggleWeekday(['mon'], 'weekend')).toEqual(['weekend']);
  });
});

describe('slots and the timeline', () => {
  it('parses times and refuses junk', () => {
    expect(parseTime('06:30')).toBe(390);
    expect(parseTime('6:30:30')).toBe(390.5);
    expect(parseTime('25:00')).toBeNull();
    expect(parseTime('sunset')).toBeNull();
  });

  it('sorts slots and stops each at the next start, the last at the first', () => {
    const out = normaliseSlots<{ start: string; stop?: string | null }>([{ start: '22:00' }, { start: '06:00:00' }, { start: 'bad' }]);
    expect(out.map((s) => [s.start, s.stop])).toEqual([['06:00:00', '22:00:00'], ['22:00:00', '06:00:00']]);
  });

  it('splits the slot that crosses midnight across both ends of the day', () => {
    const segs = daySegments(normaliseSlots([{ start: '06:00' }, { start: '22:00' }]));
    expect(segs.map((s) => [s.slot, s.start, s.end, s.carried])).toEqual([
      [1, 0, 360, true], [0, 360, 1320, false], [1, 1320, 1440, false],
    ]);
    expect(segs[0].left).toBe(0);
    expect(segs[1].left).toBeCloseTo(25);
    expect(segs[1].width).toBeCloseTo(((1320 - 360) / 1440) * 100);
    expect(segs.reduce((sum, s) => sum + s.width, 0)).toBeCloseTo(100);
  });

  it('gives one slot the whole day and nothing for no slots', () => {
    expect(daySegments([{ start: '00:00:00' }]).map((s) => [s.start, s.end])).toEqual([[0, 1440]]);
    expect(daySegments([{ start: '09:00' }]).map((s) => [s.start, s.end])).toEqual([[0, 540], [540, 1440]]);
    expect(daySegments([])).toEqual([]);
  });

  it('finds which program covers each day of the week', () => {
    const now = new Date(2026, 10, 11); // a Wednesday
    const week = weekPrograms([program(), program({ id: 'p2', name: 'Weekend', weekdays: ['weekend'] })], 'climate.hall', now);
    expect(week.map((p) => p?.id ?? null)).toEqual(['p1', 'p1', 'p1', 'p1', 'p1', 'p2', 'p2']);
    expect(weekPrograms([program()], 'climate.den', now).every((p) => p === null)).toBe(true);
  });

  it('respects date ranges and the on switch', () => {
    const wed = new Date(2026, 10, 11);
    expect(programCovers(program({ start_date: '2026-11-12' }), wed)).toBe(false);
    expect(programCovers(program({ end_date: '2026-11-11' }), wed)).toBe(true);
    expect(programCovers(program({ enabled: false }), wed)).toBe(false);
  });
});

describe('hold', () => {
  it('offers until-next-change only when something is next', () => {
    expect(holdUntilOptions(thermostat())[0]).toMatchObject({ value: 'next_slot', disabled: true });
    const next = thermostat({ next: { program_id: 'p1', program_name: 'Week', slot: 1, at: '2026-11-11T14:00:00', setpoint: { hvac_mode: 'off' } } });
    expect(holdUntilOptions(next)[0].disabled).toBeFalsy();
    expect(holdUntilOptions(next).map((o) => o.value)).toEqual(['next_slot', 'time', 'permanent']);
  });
  it('turns a choice into what the hub takes', () => {
    expect(holdUntilValue('next_slot')).toBe('next_slot');
    expect(holdUntilValue('permanent')).toBe('permanent');
    expect(holdUntilValue('time', '18:30')).toBe('18:30');
    expect(holdUntilValue('time', '')).toBeNull();
  });
});

describe('scheduler starts and sensors', () => {
  it('round-trips sun offsets', () => {
    expect(parseSlotStart('sunset-00:30')).toEqual({ kind: 'sunset', offset: -30 });
    expect(formatSlotStart({ kind: 'sunrise', offset: 75 })).toBe('sunrise+01:15');
    expect(formatSlotStart(parseSlotStart('sunset'))).toBe('sunset');
    expect(formatSlotStart(parseSlotStart('07:05:00'))).toBe('07:05:00');
  });
  it('recognises power sensors by class or unit', () => {
    expect(isPowerSensor({ entity_id: 'sensor.mains', device_class: 'power' })).toBe(true);
    expect(isPowerSensor({ entity_id: 'sensor.mains', unit: 'kW' })).toBe(true);
    expect(isPowerSensor({ entity_id: 'sensor.temp', unit: '°F' })).toBe(false);
    expect(isPowerSensor({ entity_id: 'switch.x', device_class: 'power' })).toBe(false);
  });
});
