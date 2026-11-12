import { describe, expect, it } from 'vitest';
import {
  binaryLabel, describePeriod, formatKwh, formatState, groupByDomain, groupByFloor, isNumericSeries,
  mergeNumericSeries, numericPoints, parseEntityParam, shiftDate, tariffPeriodError, timelineSegments, toggleIn,
  touSplits, type EnergySummary, type HubEntity, type TariffPeriod,
} from './dashboards';

const ent = (entity_id: string, extra: Partial<HubEntity> = {}): HubEntity => ({
  entity_id, domain: entity_id.split('.')[0], name: entity_id.split('.')[1], state: 'on', attributes: {},
  device_class: null, unit: null, icon: null, area_id: 'a1', device_id: null, last_changed: '2026-01-01T00:00:00Z',
  ...extra,
});

describe('rooms helpers', () => {
  it('groups by domain in the order people use them, names sorted', () => {
    const groups = groupByDomain([
      ent('sensor.b_temp'), ent('light.zeta'), ent('climate.hall'), ent('light.alpha'), ent('weird.thing'),
    ]);
    expect(groups.map((g) => g.domain)).toEqual(['climate', 'light', 'sensor', 'weird']);
    expect(groups[1].entities.map((e) => e.name)).toEqual(['alpha', 'zeta']);
    expect(groups[1].label).toBe('Lights');
  });

  it('groups areas under floors by level and keeps loose areas', () => {
    const rooms = {
      floors: [{ floor_id: 'up', name: 'Upper', level: 1 }, { floor_id: 'down', name: 'Lower', level: 0 }],
      areas: [
        { area_id: 'x', name: 'X', floor_id: 'up', icon: null, entities: [ent('light.x')] },
        { area_id: 'y', name: 'Y', floor_id: 'down', icon: null, entities: [ent('light.y')] },
        { area_id: 'z', name: 'Z', floor_id: null, icon: null, entities: [ent('light.z')] },
        { area_id: 'e', name: 'Empty', floor_id: 'up', icon: null, entities: [] },
      ],
      unassigned: [],
    };
    const out = groupByFloor(rooms);
    expect(out.map((g) => g.floor?.name ?? null)).toEqual(['Lower', 'Upper', null]);
    expect(out[1].areas.map((a) => a.area_id)).toEqual(['x']);
    expect(groupByFloor({ ...rooms, floors: [] })[0].areas).toHaveLength(3);
  });

  it('formats states and binary sensors by device class', () => {
    expect(formatState({ state: '21.345', unit: '°C' })).toBe('21.3 °C');
    expect(formatState({ state: '1234.5', unit: 'W' })).toBe('1235 W');
    expect(formatState({ state: 'unavailable', unit: 'W' })).toBe('Unavailable');
    expect(binaryLabel(ent('binary_sensor.door', { device_class: 'garage_door', state: 'on' }))).toBe('Open');
    expect(binaryLabel(ent('binary_sensor.m', { device_class: 'motion', state: 'off' }))).toBe('Clear');
  });
});

describe('history helpers', () => {
  it('tells numeric from state series', () => {
    expect(isNumericSeries([{ t: 'x', s: '1.5' }, { t: 'y', s: 'unavailable' }])).toBe(true);
    expect(isNumericSeries([{ t: 'x', s: 'on' }])).toBe(false);
    expect(isNumericSeries([{ t: 'x', s: 'unavailable' }])).toBe(false);
  });

  it('merges series on time, holding values and breaking on unavailable', () => {
    const a = numericPoints([{ t: '2026-01-01T00:00:00Z', s: '1' }, { t: '2026-01-01T00:02:00Z', s: 'unavailable' }]);
    const b = numericPoints([{ t: '2026-01-01T00:01:00Z', s: '5' }]);
    const rows = mergeNumericSeries({ a, b });
    expect(rows.map((r) => [r.a, r.b])).toEqual([[1, null], [1, 5], [null, 5]]);
  });

  it('reads attributes for thermostat curves', () => {
    const pts = numericPoints([{ t: '2026-01-01T00:00:00Z', s: 'cool', a: { current_temperature: 77 } }], 'current_temperature');
    expect(pts[0].v).toBe(77);
  });

  it('builds clipped, merged timeline segments', () => {
    const start = Date.parse('2026-01-01T00:00:00Z');
    const end = start + 4000;
    const segs = timelineSegments([
      { t: new Date(start - 1000).toISOString(), s: 'off' },
      { t: new Date(start + 1000).toISOString(), s: 'on' },
      { t: new Date(start + 2000).toISOString(), s: 'on' },
    ], start, end);
    expect(segs).toEqual([{ state: 'off', from: start, to: start + 1000 }, { state: 'on', from: start + 1000, to: end }]);
  });

  it('cleans the entity list from the URL', () => {
    expect(parseEntityParam('sensor.a,bad id,sensor.a,light.b')).toEqual(['sensor.a', 'light.b']);
    expect(parseEntityParam(Array.from({ length: 20 }, (_, i) => `sensor.s${i}`).join(','))).toHaveLength(12);
  });
});

describe('energy helpers', () => {
  const period: TariffPeriod = { name: 'Peak', price: 0.2, months: [5, 6, 7], weekdays: [0, 1, 2, 3, 4], start: '14:00', end: '20:00' };

  it('validates tariff periods like the hub does', () => {
    expect(tariffPeriodError(period)).toBeNull();
    expect(tariffPeriodError({ ...period, name: ' ' })).toMatch(/name/);
    expect(tariffPeriodError({ ...period, start: '25:00' })).toMatch(/Start/);
    expect(tariffPeriodError({ ...period, end: '24:30' })).toMatch(/End/);
    expect(tariffPeriodError({ ...period, end: '24:00' })).toBeNull();
    expect(tariffPeriodError({ ...period, end: '14:00' })).toMatch(/differ/);
    expect(tariffPeriodError({ ...period, price: -1 })).toMatch(/Price/);
  });

  it('describes a period from its data and toggles chips', () => {
    expect(describePeriod(period)).toBe('Mon-Fri, May-Jul, 14:00-20:00');
    expect(describePeriod({ ...period, weekdays: [], months: [] })).toBe('Every day, all year, 14:00-20:00');
    expect(toggleIn([1, 3], 2)).toEqual([1, 2, 3]);
    expect(toggleIn([1, 2, 3], 2)).toEqual([1, 3]);
  });

  it('steps ranges', () => {
    const d = new Date(2026, 4, 31);
    expect(shiftDate('month', d, -1).getMonth()).toBe(3);
    expect(shiftDate('day', d, 1).getDate()).toBe(1);
    expect(shiftDate('year', d, 1).getFullYear()).toBe(2027);
    expect(formatKwh(1234)).toBe('1.23 MWh');
    expect(formatKwh(12.34)).toBe('12.3 kWh');
  });

  it('offers the tariff split and each utility split that has data', () => {
    const summary = {
      tou: { source: 'tariff', periods: [{ name: 'Peak', kwh: 3, cost: 1 }] },
      utility: [
        { name: 'Meter', periods: [{ name: 'on_peak', stat: 'x', kwh: 2 }, { name: 'off_peak', stat: 'y', kwh: 5 }] },
        { name: 'Empty', periods: [{ name: 'on_peak', stat: 'z', kwh: 0 }] },
      ],
    } as unknown as EnergySummary;
    const splits = touSplits(summary);
    expect(splits.map((s) => s.source)).toEqual(['tariff', 'utility']);
    expect(splits[1].periods.map((p) => p.name)).toEqual(['On peak', 'Off peak']);
  });
});
