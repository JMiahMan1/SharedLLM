import { describe, expect, it } from 'vitest';
import type { HubEntity } from './dashboards';
import {
  climateFeatures, controlKind, coverFeatures, keyCommand, lightEffects, mapImage, mediaFeatures, mediaPosition, powerReadings,
  sirenFeatures, tvApps, usedSiblings, vacuumFeatures, vacuumReadings,
} from './deviceControls';

const ent = (entity_id: string, state: string, over: Partial<HubEntity> = {}): HubEntity => ({
  entity_id, domain: entity_id.split('.')[0], name: entity_id, state, attributes: {}, device_class: null, unit: null,
  icon: null, area_id: null, device_id: 'd1', last_changed: '', ...over,
});

describe('the registry', () => {
  it('chooses a control from the entity alone', () => {
    expect(controlKind(ent('light.a', 'on'))).toBe('light');
    expect(controlKind(ent('switch.a', 'on'))).toBe('switch');
    expect(controlKind(ent('media_player.a', 'on', { device_class: 'tv' }))).toBe('tv');
    expect(controlKind(ent('media_player.a', 'on', { device_class: 'speaker' }))).toBe('speaker');
    expect(controlKind(ent('media_player.a', 'on', { name: 'Den Chromecast' }))).toBe('tv');
    expect(controlKind(ent('cover.a', 'closed', { device_class: 'garage' }))).toBe('garage');
    expect(controlKind(ent('cover.a', 'closed', { device_class: 'blind' }))).toBe('cover');
    for (const [id, kind] of [['vacuum.a', 'vacuum'], ['camera.a', 'camera'], ['climate.a', 'climate'], ['lock.a', 'lock'],
      ['siren.a', 'siren'], ['button.a', 'button'], ['number.a', 'number'], ['select.a', 'select'], ['time.a', 'time'],
      ['update.a', 'update'], ['sensor.a', 'sensor'], ['binary_sensor.a', 'binary'], ['image.a', 'image'],
      ['remote.a', 'remote'], ['fan.a', 'fan'], ['scene.a', 'scene'], ['weather.a', 'other']]) {
      expect(controlKind(ent(id, 'on'))).toBe(kind);
    }
  });
});

describe('features from supported_features', () => {
  it('reads a media player the way Home Assistant defines it', () => {
    // A real TV's bits: TURN_ON|TURN_OFF|VOLUME_STEP|VOLUME_MUTE|VOLUME_SET|PLAY_MEDIA|SELECT_SOURCE|BROWSE|...
    const f = mediaFeatures(ent('media_player.a', 'on', { attributes: { supported_features: 128 | 256 | 1024 | 8 | 4 | 2048,
      source_list: ['HDMI 1', 'Apps'] } }));
    expect(f).toMatchObject({ power: true, volume: true, volumeStep: true, mute: true, previous: false, sources: ['HDMI 1', 'Apps'] });
    expect(mediaFeatures(ent('media_player.a', 'on', { attributes: { supported_features: 0 } }))).toMatchObject({ power: false, play: false });
  });
  it('reads a robot vacuum (the bits a Roborock reports)', () => {
    const f = vacuumFeatures(ent('vacuum.a', 'docked', { attributes: { supported_features: 30524, fan_speed_list: ['quiet', 'max'] } }));
    expect(f).toEqual({ start: true, pause: true, stop: true, home: true, locate: true, spot: true,
      fanSpeeds: ['quiet', 'max'], rooms: true });
  });
  it('reads a thermostat: one set point or a range, its modes, fan and presets', () => {
    const f = climateFeatures(ent('climate.a', 'heat_cool', { attributes: { supported_features: 399, hvac_modes: ['off', 'heat'],
      fan_modes: ['auto'], target_temp_low: 68, target_temp_high: 76, max_temp: 95 } }));
    expect(f).toMatchObject({ single: true, range: true, modes: ['off', 'heat'], fanModes: ['auto'], presets: [], max: 95 });
  });
  it('reads covers, sirens and light effects', () => {
    expect(coverFeatures(ent('cover.a', 'open', { attributes: { supported_features: 15 } }))).toEqual({ open: true, close: true, position: true, stop: true });
    expect(coverFeatures(ent('cover.a', 'open', { attributes: { supported_features: 3 } }))).toMatchObject({ position: false, stop: false });
    expect(sirenFeatures(ent('siren.a', 'off', { attributes: { supported_features: 7, available_tones: { 1: 'Bell', 2: 'Alarm' } } })))
      .toMatchObject({ on: true, off: true, tones: ['Bell', 'Alarm'] });
    expect(lightEffects(ent('light.a', 'on', { attributes: { supported_features: 44, effect_list: ['Rainbow'] } }))).toEqual(['Rainbow']);
    expect(lightEffects(ent('light.a', 'on', { attributes: { supported_features: 32, effect_list: ['Rainbow'] } }))).toEqual([]);
  });
});

describe('a TV remote', () => {
  it('sends the name the remote knows for each key', () => {
    const roku = ent('remote.a', 'on', { attributes: { command_list: ['up', 'select', 'home', 'back'] } });
    expect(keyCommand(roku, 'up')).toBe('up');
    expect(keyCommand(roku, 'ok')).toBe('select');
    expect(keyCommand(roku, 'channel_up')).toBeNull();
    // No list (Android TV Remote): the common name, and the hub decides.
    expect(keyCommand(ent('remote.b', 'on'), 'ok')).toBe('DPAD_CENTER');
  });
  it('offers its sources, else its remote\'s activities', () => {
    expect(tvApps(ent('media_player.a', 'on', { attributes: { source_list: ['TV'], source: 'TV' } }), null))
      .toEqual({ via: 'source', options: ['TV'], current: 'TV' });
    expect(tvApps(ent('media_player.a', 'on'), ent('remote.a', 'on', { attributes: { activity_list: ['Video'], current_activity: 'Video' } })))
      .toEqual({ via: 'activity', options: ['Video'], current: 'Video' });
  });
  it('runs the position on while playing', () => {
    const at = '2026-11-16T12:00:00Z';
    const e = ent('media_player.a', 'playing', { attributes: { media_position: 10, media_duration: 100, media_position_updated_at: at } });
    expect(mediaPosition(e, Date.parse(at) + 5000)).toBe(15);
    expect(mediaPosition({ ...e, state: 'paused' }, Date.parse(at) + 5000)).toBe(10);
  });
});

describe("the device's other entities", () => {
  const siblings = [
    ent('sensor.p', '12', { device_class: 'power', unit: 'W' }), ent('sensor.e', '3', { device_class: 'energy', unit: 'kWh' }),
    ent('image.robot_map', '2026-11-16'), ent('image.robot_other', '2026-11-16'), ent('sensor.b', '80', { device_class: 'battery' }),
    { ...ent('sensor.robot_filter_time_left', '40'), name: 'Robot Filter time left' },
    { ...ent('sensor.robot_vacuum_error', 'none'), name: 'Robot Vacuum error' },
    ent('remote.tv', 'on'),
  ];
  it('finds a plug\'s power and energy, a vacuum\'s map and parts', () => {
    expect(powerReadings(siblings)).toMatchObject({ power: { entity_id: 'sensor.p' }, energy: { entity_id: 'sensor.e' } });
    expect(mapImage(siblings)?.entity_id).toBe('image.robot_map');
    const r = vacuumReadings(siblings);
    expect(r.battery?.entity_id).toBe('sensor.b');
    expect(r.errors.map((x) => x.entity_id)).toEqual(['sensor.robot_vacuum_error']);
    expect(r.wear.map((x) => x.entity_id)).toEqual(['sensor.robot_filter_time_left']);
  });
  it('says which of them the control already shows', () => {
    expect(usedSiblings(ent('switch.a', 'on'), siblings).sort()).toEqual(['sensor.e', 'sensor.p']);
    expect(usedSiblings(ent('media_player.tv', 'on', { device_class: 'tv' }), siblings)).toEqual(['remote.tv']);
    expect(usedSiblings(ent('vacuum.a', 'docked'), siblings)).toContain('image.robot_map');
  });
});
