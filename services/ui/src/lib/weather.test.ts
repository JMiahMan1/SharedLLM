import { describe, expect, it } from 'vitest';
import { Cloud, CloudRain, Moon, Sun } from 'lucide-react';
import {
  cleanReminder, convertThreshold, dayLabel, hourLabel, inUnits, parseLocation, parseReminders, removeReminder, unitSuffix, upsertReminder,
  weatherIcon, type WeatherReminder,
} from './weather';

describe('weather reminders setting', () => {
  it('keeps valid rules and drops the rest', () => {
    const rules = parseReminders(JSON.stringify([
      { id: 'Hay', kind: 'rain', title: ' Cover the hay ', people: [' Alex ', ''], threshold: '70' },
      { kind: 'tornado', title: 'nope' },
      'junk',
      { id: 'hay', kind: 'wind' },
      { id: 'pipes', kind: 'freeze', location: { name: 'Barn', latitude: '36.1', longitude: -86.7 }, enabled: false },
    ]));
    expect(rules).toEqual([
      { id: 'hay', kind: 'rain', title: 'Cover the hay', people: ['alex'], threshold: 70 },
      {
        id: 'pipes', kind: 'freeze', title: 'Freeze coming', people: [],
        location: { name: 'Barn', latitude: 36.1, longitude: -86.7 }, enabled: false,
      },
    ]);
    expect(parseReminders('not json')).toEqual([]);
    expect(parseReminders(undefined)).toEqual([]);
  });

  it('cleans, upserts by id and removes', () => {
    const rule: WeatherReminder = { id: 'hay', kind: 'rain', title: '', people: [], hours: 99, note: '  ' };
    expect(cleanReminder(rule)).toEqual({ id: 'hay', kind: 'rain', title: 'Rain on the way', people: [], hours: 48 });
    const list = upsertReminder([], rule);
    const edited = upsertReminder(list, { ...rule, title: 'Cover the hay' });
    expect(edited).toHaveLength(1);
    expect(edited[0].title).toBe('Cover the hay');
    expect(removeReminder(edited, 'hay')).toEqual([]);
  });

  it('reads a location only when it is a real place', () => {
    expect(parseLocation('{"latitude": 36, "longitude": -86.8}')).toEqual({ latitude: 36, longitude: -86.8 });
    expect(parseLocation({ latitude: 91, longitude: 0 })).toBeNull();
    expect(parseLocation('')).toBeNull();
  });
});

describe('weather wording', () => {
  it('labels hours in the forecast zone and days by weekday', () => {
    // 21:00 UTC is 3 PM at UTC-6, whatever zone the test runs in.
    expect(hourLabel(Date.UTC(2026, 10, 3, 21) / 1000, -6 * 3600)).toBe('3 PM');
    expect(hourLabel(Date.UTC(2026, 10, 3, 6) / 1000, -6 * 3600)).toBe('12 AM');
    expect(dayLabel('2026-11-03', 0)).toBe('Today');
    expect(dayLabel('2026-11-04', 1)).toMatch(/Wed/);
  });

  it('picks icons, with a moon for a clear night', () => {
    expect(weatherIcon('clear')).toBe(Sun);
    expect(weatherIcon('clear', false)).toBe(Moon);
    expect(weatherIcon('rain')).toBe(CloudRain);
    expect(weatherIcon('unknown')).toBe(Cloud);
  });
});

describe('weather integrations', () => {
  it('keeps only actions the server will run', async () => {
    const { parseActions } = await import('./weather');
    expect(parseActions([
      { type: 'ha_service', service: 'Cover.Close_Cover', entity_id: 'cover.barn_door' },
      { type: 'ha_service', service: 'close the door' },
      { type: 'call', service: 'geo', path: '/api/hooks/weather' },
      { type: 'call', service: 'geo', path: 'http://evil.example/' },
      { type: 'call', service: 'geo', path: '/a/../b' },
      { type: 'shell', command: 'rm -rf /' },
    ])).toEqual([
      { type: 'ha_service', service: 'cover.close_cover', entity_id: 'cover.barn_door' },
      { type: 'call', service: 'geo', path: '/api/hooks/weather', method: 'POST' },
    ]);
  });

  it('reads sensor metrics and hooks, and names metrics from labels', async () => {
    const { metricName, parseHooks, parseSensorMetrics } = await import('./weather');
    expect(metricName('Solar panels')).toBe('solar_panels');
    expect(metricName('2nd barn')).toBe('m_2nd_barn');
    expect(parseSensorMetrics(JSON.stringify([
      { name: 'solar', label: 'Solar', entity_id: 'sensor.solar', mode: 'power' },
      { name: 'Bad', entity_id: 'sensor.x', mode: 'power' },
      { name: 'x', entity_id: 'sensor.x', mode: 'vibes' },
    ]))).toEqual([{ name: 'solar', label: 'Solar', entity_id: 'sensor.solar', mode: 'power' }]);
    expect(parseHooks(JSON.stringify([
      { id: 'a', events: ['alert.started', 'nope'], service: 'geo', path: '/h' },
      { events: [], service: 'geo', path: '/h' },
    ]))).toEqual([{ id: 'a', events: ['alert.started'], service: 'geo', path: '/h', method: 'POST', filter: { kinds: [], rules: [] } }]);
  });
});

describe('reminder thresholds across units', () => {
  it('converts each measure and leaves percentages alone', () => {
    expect(convertThreshold('temp', 32, 'imperial', 'metric')).toBe(0);
    expect(convertThreshold('temp', 35, 'metric', 'imperial')).toBe(95);
    expect(convertThreshold('wind', 65, 'metric', 'imperial')).toBe(40.4);
    expect(convertThreshold('snow', 1, 'imperial', 'metric')).toBe(2.5);
    expect(convertThreshold('percent', 60, 'imperial', 'metric')).toBe(60);
    expect(unitSuffix('snow', 'metric')).toBe('cm');
  });

  it('shows a rule in the household units and stamps the units it is saved in', () => {
    const rule = { id: 'p', kind: 'freeze' as const, title: '', people: [], threshold: 32, units: 'imperial' as const };
    expect(inUnits(rule, 'metric')).toMatchObject({ threshold: 0, units: 'metric' });
    expect(inUnits({ ...rule, units: undefined }, 'metric')).toMatchObject({ threshold: 32, units: 'metric' });
    expect(cleanReminder(inUnits(rule, 'metric')).units).toBe('metric');
    expect(parseReminders(JSON.stringify([{ id: 'p', kind: 'freeze', units: 'metric', threshold: 0 }]))[0].units).toBe('metric');
  });
});
