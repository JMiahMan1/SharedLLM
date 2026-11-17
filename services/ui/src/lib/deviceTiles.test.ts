import { describe, expect, it } from 'vitest';
import type { HubEntity } from './dashboards';
import {
  entitySection, expectedState, isOffline, kelvinToRgb, lightColour, lightFeatures, plainName, primaryEntity, stateLine,
  tileAction, tileKind, typeFilterOf, withChange,
} from './deviceTiles';

const NOW = Date.parse('2026-11-16T12:00:00Z');
const ent = (entity_id: string, state: string, over: Partial<HubEntity> = {}): HubEntity => ({
  entity_id, domain: entity_id.split('.')[0], name: entity_id, state, attributes: {}, device_class: null, unit: null,
  icon: null, area_id: null, device_id: 'd1', last_changed: new Date(NOW - 10 * 60_000).toISOString(), ...over,
});
const online = { status: 'online' as const, last_seen: new Date(NOW - 60_000).toISOString() };

describe('which entity speaks for a device', () => {
  it('picks the control over its readings, and the most telling reading for a sensor', () => {
    const light = ent('light.a', 'on');
    expect(primaryEntity('light', [ent('sensor.power', '3'), light])).toBe(light);
    const temp = ent('sensor.t', '21', { device_class: 'temperature' });
    expect(primaryEntity('sensor', [ent('sensor.b', '90', { device_class: 'battery' }), temp])).toBe(temp);
    const player = ent('media_player.tv', 'on');
    expect(primaryEntity('media', [ent('remote.tv', 'on'), player])).toBe(player);
    expect(primaryEntity('light', [])).toBeNull();
  });
});

describe('tile kinds', () => {
  const dev = { name: 'Thing', model: null, manufacturer: null };
  it('tells a TV from a speaker by device class, then by its words', () => {
    expect(tileKind({ ...dev, kind: 'media' }, ent('media_player.x', 'on', { device_class: 'tv' }))).toBe('tv');
    expect(tileKind({ ...dev, kind: 'media' }, ent('media_player.x', 'on', { device_class: 'speaker' }))).toBe('speaker');
    expect(tileKind({ ...dev, name: 'Den Roku', kind: 'media' }, ent('media_player.x', 'on'))).toBe('tv');
    expect(tileKind({ ...dev, kind: 'media' }, ent('media_player.x', 'on'))).toBe('media');
  });
  it('sharpens the hub kind by the main entity, and works without a hub kind', () => {
    expect(tileKind({ ...dev, kind: 'access' }, ent('lock.x', 'locked'))).toBe('lock');
    expect(tileKind({ ...dev, kind: 'access' }, ent('cover.x', 'open'))).toBe('cover');
    expect(tileKind({ ...dev, kind: 'climate' }, ent('fan.x', 'on'))).toBe('fan');
    expect(tileKind({ ...dev, kind: 'power' }, ent('switch.x', 'on'))).toBe('switch');
    expect(tileKind(dev, ent('vacuum.x', 'docked'))).toBe('vacuum');
    expect(typeFilterOf('fan')).toBe('switches');
  });
});

describe('state lines', () => {
  it('says what a person needs in words', () => {
    expect(stateLine(online, ent('light.a', 'on', { attributes: { brightness: 181 } }), NOW)).toBe('On, 71%');
    expect(stateLine(online, ent('light.a', 'off'), NOW)).toBe('Off');
    expect(stateLine(online, ent('sensor.t', '21.53', { unit: '°C' }), NOW)).toBe('21.5 °C');
    expect(stateLine(online, ent('binary_sensor.d', 'on', { device_class: 'door' }), NOW)).toBe('Open');
    expect(stateLine(online, ent('media_player.s', 'playing', { attributes: { media_title: 'Song' } }), NOW)).toBe('Playing Song');
    expect(stateLine(online, ent('lock.f', 'locked'), NOW)).toBe('Locked');
    expect(stateLine(online, ent('cover.b', 'open', { attributes: { current_position: 40 } }), NOW)).toBe('Open 40%');
    expect(stateLine(online, ent('climate.h', 'heat', { attributes: { temperature: 70, temperature_unit: '°F' } }), NOW))
      .toBe('Heat to 70°F');
    expect(stateLine(online, ent('climate.h', 'heat_cool', { attributes: { target_temp_low: 68, target_temp_high: 76 } }), NOW))
      .toBe('Heat and cool, 68 to 76°');
    expect(stateLine(online, ent('vacuum.r', 'docked', { attributes: { battery_level: 90 } }), NOW)).toBe('Docked, 90% battery');
    expect(stateLine(online, null, NOW)).toBe('Online');
  });
  it('says an offline device is offline, and for how long', () => {
    expect(stateLine({ status: 'offline', last_seen: new Date(NOW - 3 * 60_000).toISOString() }, null, NOW)).toBe('Offline for 3 min');
    expect(stateLine(online, ent('light.a', 'unavailable', { last_changed: new Date(NOW - 3 * 3600_000).toISOString() }), NOW))
      .toBe('Offline for 3 h');
    expect(stateLine({ status: 'disabled', last_seen: null }, null, NOW)).toBe('Turned off in settings');
    expect(isOffline({ status: 'partial' }, ent('switch.a', 'on'))).toBe(false);
    expect(isOffline({ status: 'online' }, ent('switch.a', 'unavailable'))).toBe(true);
  });
});

describe('one-tap actions', () => {
  it('toggles lights and switches, plays and pauses players, and leaves locks to the detail', () => {
    expect(tileAction(ent('light.a', 'on'), 'Lamp')).toEqual({ kind: 'toggle', on: true, service: 'turn_off', label: 'Lamp power' });
    expect(tileAction(ent('switch.a', 'off'), 'Plug')).toMatchObject({ service: 'turn_on' });
    expect(tileAction(ent('media_player.a', 'playing'), 'TV')).toMatchObject({ kind: 'playpause', service: 'media_pause' });
    expect(tileAction(ent('media_player.a', 'off'), 'TV')).toBeNull();
    expect(tileAction(ent('lock.a', 'locked'), 'Door')).toBeNull();
    expect(tileAction(ent('light.a', 'unavailable'), 'Lamp')).toBeNull();
  });
  it('draws the state a command is expected to leave', () => {
    const lamp = ent('light.a', 'off', { attributes: { brightness: 10 } });
    expect(withChange(lamp, expectedState(lamp, 'turn_on', { brightness_pct: 100 })))
      .toMatchObject({ state: 'on', attributes: { brightness: 255 } });
    expect(expectedState(lamp, 'turn_on', { rgb_color: [1, 2, 3] })?.attributes).toMatchObject({ color_mode: 'rgb' });
    expect(expectedState(ent('climate.a', 'off'), 'set_hvac_mode', { hvac_mode: 'heat' })).toEqual({ state: 'heat' });
    expect(expectedState(ent('vacuum.a', 'docked'), 'start')).toEqual({ state: 'cleaning' });
    expect(expectedState(lamp, 'unknown_service')).toBeNull();
  });
});

describe('lights', () => {
  it('offers brightness, white and colour only when the colour modes include them', () => {
    expect(lightFeatures(ent('light.a', 'on', { attributes: { supported_color_modes: ['color_temp', 'rgb'],
      min_color_temp_kelvin: 2000, max_color_temp_kelvin: 6535 } })))
      .toEqual({ brightness: true, colorTemp: true, color: true, minKelvin: 2000, maxKelvin: 6535 });
    expect(lightFeatures(ent('light.a', 'on', { attributes: { supported_color_modes: ['brightness'] } })))
      .toMatchObject({ brightness: true, colorTemp: false, color: false });
    expect(lightFeatures(ent('light.a', 'on', { attributes: { supported_color_modes: ['onoff'] } })))
      .toMatchObject({ brightness: false, colorTemp: false, color: false });
    // Mireds from an older integration.
    expect(lightFeatures(ent('light.a', 'on', { attributes: { supported_color_modes: ['color_temp'], min_mireds: 153, max_mireds: 500 } })))
      .toMatchObject({ minKelvin: 2000, maxKelvin: 6536 });
  });
  it('tints a lit light with its own colour, and a white one with its temperature', () => {
    expect(lightColour(ent('light.a', 'on', { attributes: { color_mode: 'rgb', rgb_color: [255, 0, 0] } }))).toEqual([255, 0, 0]);
    expect(lightColour(ent('light.a', 'on', { attributes: { color_mode: 'color_temp', color_temp_kelvin: 2700, rgb_color: [1, 1, 1] } })))
      .toEqual(kelvinToRgb(2700));
    expect(lightColour(ent('light.a', 'off', { attributes: { rgb_color: [255, 0, 0] } }))).toBeNull();
  });
});

describe('the detail', () => {
  it('splits entities like Home Assistant does, in plain names', () => {
    expect(entitySection({ domain: 'light', entity_category: null, disabled_by: null })).toBe('controls');
    expect(entitySection({ domain: 'sensor', entity_category: null, disabled_by: null })).toBe('sensors');
    expect(entitySection({ domain: 'number', entity_category: 'config', disabled_by: null })).toBe('config');
    expect(entitySection({ domain: 'sensor', entity_category: 'diagnostic', disabled_by: null })).toBe('diagnostic');
    expect(entitySection({ domain: 'button', entity_category: 'diagnostic', disabled_by: null })).toBe('config');
    expect(entitySection({ domain: 'sensor', entity_category: null, disabled_by: 'user' })).toBe('disabled');
    expect(plainName('Desk bulb Default fade', 'Desk bulb')).toBe('Default fade');
    expect(plainName('Desk bulb', 'Desk bulb')).toBe('Desk bulb');
    expect(plainName('Power', 'Desk bulb')).toBe('Power');
  });
});
