import type { Page } from '@playwright/test';

/**
 * The device hub's hardware list for the Devices page (services/homehub/hardware.py), made up:
 * invented names, RFC 5737 addresses and 02:00:00 MACs. Used by homeMocks.ts, and with
 * `mockDeviceStates` by the Devices specs, which also need the devices' live states.
 */

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

const device = (over: Record<string, unknown>) => ({
  original_name: null, name_by_user: null, model_id: null, hw_version: null, serial_number: null,
  entry_state: 'loaded', entry_reason: null, configuration_url: null, entry_type: null, via_device_id: null,
  via_device_name: null, disabled_by: null, entities: 2, unavailable: 0, status: 'online', last_seen: ago(3),
  labels: [], ...over,
});

export const HARDWARE = {
  areas: [{ area_id: 'kitchen', name: 'Kitchen' }, { area_id: 'living', name: 'Living room' }, { area_id: 'study', name: 'Study' },
    { area_id: 'den', name: 'Den' }, { area_id: 'porch', name: 'Front porch' }, { area_id: 'garage', name: 'Garage' }],
  devices: [
    device({ id: 'hw-lamp', name: 'Reading lamp with a rather long name for small phones', manufacturer: 'WiZ',
      model: 'ESP25_SHRGB_01', sw_version: '1.36.1', area_id: 'living', area: 'Living room', domain: 'wiz', label: 'WiZ',
      entry_id: 'e-wiz', entry_title: 'Reading lamp', abilities: ['color', 'color_temp', 'dim', 'power', 'sense'], kind: 'light',
      connections: [{ type: 'ip', value: '192.0.2.21' }, { type: 'mac', value: '02:00:00:00:00:21' }] }),
    device({ id: 'hw-tv', name: 'Living TV', manufacturer: 'Example', model: 'Streamer', area_id: 'living',
      area: 'Living room', domain: 'roku', label: 'Roku', entry_id: 'e-roku', entry_title: 'Living TV', kind: 'media',
      status: 'offline', unavailable: 2, last_seen: ago(185), abilities: ['launch_app', 'play_media', 'power'],
      connections: [{ type: 'ip', value: '192.0.2.66' }] }),
    device({ id: 'hw-plug', name: 'Kettle plug', manufacturer: 'Espressif', model: 'Plug', area_id: 'kitchen',
      area: 'Kitchen', domain: 'esphome', label: 'ESPHome', entry_id: 'e-esp', entry_title: 'Kettle plug', kind: 'power',
      status: 'partial', unavailable: 1, abilities: ['power', 'sense'], connections: [{ type: 'mac', value: '02:00:00:00:00:31' }] }),
    device({ id: 'hw-bulb', name: 'Desk bulb', manufacturer: 'Example Lighting', model: 'A15 colour bulb', area_id: 'study',
      area: 'Study', domain: 'esphome', label: 'ESPHome', entry_id: 'e-esp2', entry_title: 'desk-bulb', kind: 'light',
      sw_version: '2026.10.1', entities: 5, abilities: ['color', 'color_temp', 'dim', 'power', 'set_value', 'select_option'],
      connections: [{ type: 'ip', value: '192.0.2.41' }, { type: 'mac', value: '02:00:00:00:00:41' }] }),
    device({ id: 'hw-speaker', name: 'Kitchen speaker', manufacturer: 'Example Audio', model: 'Speaker One', area_id: 'kitchen',
      area: 'Kitchen', domain: 'music_assistant', label: 'Music Assistant', entry_id: 'e-ma', entry_title: 'Music',
      kind: 'media', abilities: ['play_media', 'volume', 'power'], connections: [] }),
    device({ id: 'hw-sensor', name: 'Hall climate sensor', manufacturer: 'Example', model: 'TH-1', area_id: null, area: null,
      domain: 'zha', label: 'Zigbee', entry_id: 'e-zha', entry_title: 'Zigbee', kind: 'sensor', abilities: ['sense'],
      connections: [{ type: 'zigbee', value: '00:00:00:00:00:00:00:51' }] }),
    device({ id: 'hw-den-tv', name: 'Den TV', manufacturer: 'Example', model: 'Smart TV 55', area_id: 'den', area: 'Den',
      domain: 'androidtv_remote', label: 'Android TV Remote', entry_id: 'e-atv', entry_title: 'Den TV', kind: 'media',
      abilities: ['power', 'volume', 'send_command'], connections: [{ type: 'ip', value: '192.0.2.71' }] }),
    device({ id: 'hw-vacuum', name: 'Robot vacuum', manufacturer: 'Example Robotics', model: 'S1', area_id: 'kitchen',
      area: 'Kitchen', domain: 'roborock', label: 'Roborock', entry_id: 'e-rr', entry_title: 'Account', kind: 'vacuum',
      entities: 7, abilities: ['vacuum_start', 'vacuum_return'], connections: [] }),
    device({ id: 'hw-camera', name: 'Porch camera', manufacturer: 'Example Security', model: 'Cam 2', area_id: 'porch',
      area: 'Front porch', domain: 'reolink', label: 'Reolink', entry_id: 'e-cam', entry_title: 'Porch camera', kind: 'camera',
      abilities: ['stream'], connections: [{ type: 'ip', value: '192.0.2.81' }] }),
    device({ id: 'hw-thermostat', name: 'Hall thermostat', manufacturer: 'Example Climate', model: 'T-9', area_id: 'living',
      area: 'Living room', domain: 'ecobee', label: 'ecobee', entry_id: 'e-eco', entry_title: 'Home', kind: 'climate',
      abilities: ['set_temperature'], connections: [] }),
    device({ id: 'hw-garage', name: 'Garage door', manufacturer: 'Example', model: 'Opener', area_id: 'garage', area: 'Garage',
      domain: 'ratgdo', label: 'ratgdo', entry_id: 'e-gdo', entry_title: 'Garage door', kind: 'access', abilities: ['open', 'close'],
      connections: [] }),
    device({ id: 'hw-lock', name: 'Front door lock', manufacturer: 'Example', model: 'Deadbolt', area_id: 'porch',
      area: 'Front porch', domain: 'zwave_js', label: 'Z-Wave', entry_id: 'e-zw', entry_title: 'Z-Wave', kind: 'access',
      abilities: ['lock'], connections: [] }),
    device({ id: 'hw-fan', name: 'Study fan', manufacturer: 'Example', model: 'Tower fan', area_id: 'study', area: 'Study',
      domain: 'esphome', label: 'ESPHome', entry_id: 'e-esp3', entry_title: 'study-fan', kind: 'climate',
      abilities: ['power', 'set_speed'], connections: [] }),
    device({ id: 'hw-service', name: 'Example weather service', manufacturer: 'Example', model: 'Forecast', area_id: null,
      area: null, domain: 'met', label: 'Weather', entry_id: 'e-met', entry_title: 'Home', kind: 'other', service: true,
      entry_type: 'service', abilities: ['sense'], connections: [] }),
  ],
};

const st = (entity_id: string, name: string, state: string, device_id: string, area_id: string | null,
  extra: Record<string, unknown> = {}) => ({
  entity_id, domain: entity_id.split('.')[0], name, state, attributes: {} as Record<string, unknown>, device_class: null,
  unit: null, icon: null, area_id, device_id, last_changed: ago(10), ...extra,
});

/** The live states, as the Rooms feed carries them (by area, each with its device_id). */
export function initialStates() {
  return {
    'light.reading_lamp': st('light.reading_lamp', 'Reading lamp', 'on', 'hw-lamp', 'living', {
      attributes: { brightness: 180, supported_color_modes: ['rgb'], color_mode: 'rgb', rgb_color: [255, 150, 70] },
      abilities: ['color', 'dim', 'power'] }),
    'media_player.living_tv': st('media_player.living_tv', 'Living TV', 'unavailable', 'hw-tv', 'living', {
      last_changed: ago(185), device_class: 'tv', offline: { cause: 'device', integration: 'Roku' } }),
    'switch.kettle_plug': st('switch.kettle_plug', 'Kettle plug', 'off', 'hw-plug', 'kitchen'),
    'light.desk_bulb': st('light.desk_bulb', 'Desk bulb', 'on', 'hw-bulb', 'study', {
      attributes: { brightness: 128, supported_color_modes: ['color_temp', 'rgb'], color_mode: 'color_temp',
        color_temp_kelvin: 2700, min_color_temp_kelvin: 2000, max_color_temp_kelvin: 6535 } }),
    'media_player.kitchen_speaker': st('media_player.kitchen_speaker', 'Kitchen speaker', 'playing', 'hw-speaker', 'kitchen', {
      device_class: 'speaker',
      attributes: { media_title: 'Morning Songs', media_artist: 'Example Choir', volume_level: 0.35, is_volume_muted: false,
        supported_features: 16384 | 1 | 4 | 8 | 16 | 32 | 128 | 256 | 2048, source_list: ['Radio', 'Playlist'], source: 'Playlist' } }),
    'media_player.den_tv': st('media_player.den_tv', 'Den TV', 'on', 'hw-den-tv', 'den', { device_class: 'tv',
      attributes: { supported_features: 128 | 256 | 1024 | 8 | 4, volume_level: 0.2, is_volume_muted: false, app_name: 'Video app' } }),
    'remote.den_tv': st('remote.den_tv', 'Den TV', 'on', 'hw-den-tv', 'den', { attributes: { supported_features: 4,
      activity_list: ['Home screen', 'Video app', 'Music app', 'News app'], current_activity: 'Video app' } }),
    'vacuum.robot_vacuum': st('vacuum.robot_vacuum', 'Robot vacuum', 'docked', 'hw-vacuum', 'kitchen', { attributes: {
      supported_features: 30524, fan_speed_list: ['quiet', 'balanced', 'turbo', 'max'], fan_speed: 'balanced',
      segments: [{ id: 16, name: 'Hall' }, { id: 17, name: 'Kitchen' }] } }),
    'camera.porch_camera': st('camera.porch_camera', 'Porch camera', 'idle', 'hw-camera', 'porch',
      { attributes: { supported_features: 2 } }),
    'climate.hall_thermostat': st('climate.hall_thermostat', 'Hall thermostat', 'heat_cool', 'hw-thermostat', 'living', {
      attributes: { supported_features: 399, hvac_modes: ['off', 'heat', 'cool', 'heat_cool'], hvac_action: 'idle',
        current_temperature: 71, current_humidity: 41, target_temp_low: 68, target_temp_high: 76, min_temp: 45, max_temp: 95,
        target_temp_step: 1, fan_modes: ['auto', 'on'], fan_mode: 'auto', temperature_unit: '°F' } }),
    'cover.garage_door': st('cover.garage_door', 'Garage door', 'closed', 'hw-garage', 'garage',
      { device_class: 'garage', attributes: { supported_features: 3 } }),
    'lock.front_door': st('lock.front_door', 'Front door lock', 'locked', 'hw-lock', 'porch'),
    'fan.study_fan': st('fan.study_fan', 'Study fan', 'on', 'hw-fan', 'study', {
      attributes: { supported_features: 1 | 2 | 8 | 16 | 32, percentage: 40, percentage_step: 10, oscillating: false,
        preset_modes: ['normal', 'sleep', 'breeze'], preset_mode: 'normal' } }),
    'sensor.hall_temperature': st('sensor.hall_temperature', 'Hall climate sensor Temperature', '21.5', 'hw-sensor', null, {
      unit: '°C', device_class: 'temperature' }),
  } as Record<string, ReturnType<typeof st>>;
}

type States = ReturnType<typeof initialStates>;

function rooms(s: States) {
  const of = (area: string | null) => Object.values(s).filter((e) => e.area_id === area);
  return {
    floors: [],
    areas: HARDWARE.areas.map((a) => ({ ...a, floor_id: null, icon: null, entities: of(a.area_id) })),
    unassigned: of(null),
  };
}

const QUIET = { exposed: false, default: false, chosen: false };

const entity = (entity_id: string, name: string, state: unknown, over: Record<string, unknown> = {}) => ({
  entity_id, domain: entity_id.split('.')[0], platform: 'example', name, name_by_user: null, original_name: null,
  entity_category: null as string | null, disabled_by: null, hidden_by: null, area_id: null, icon: null, capabilities: null,
  labels: [], voice: { exposed: true, default: true, chosen: false }, state, ...over,
});

const config = (entity_id: string, name: string, state: string, attributes: Record<string, unknown>, unit: string | null = null) =>
  entity(entity_id, name, st(entity_id, name, state, '', null, { attributes, unit }), { entity_category: 'config', voice: QUIET });

const reading = (entity_id: string, name: string, state: string, device: string, unit: string, cls: string,
  category: string | null = null) => entity(entity_id, name,
  st(entity_id, name, state, device, null, { unit, device_class: cls }), { entity_category: category, voice: QUIET });

/** Each device's detail, with its entities' current states. */
export function detailFor(id: string, s: States, voices: Map<string, boolean> = new Map()) {
  const base = HARDWARE.devices.find((d) => d.id === id) ?? HARDWARE.devices[0];
  const list: ReturnType<typeof entity>[] = [];
  if (id === 'hw-lamp' || !HARDWARE.devices.some((d) => d.id === id)) {
    list.push(entity('light.reading_lamp', 'Reading lamp', s['light.reading_lamp'],
      { capabilities: { abilities: ['color', 'dim', 'power'], services: ['light.turn_on'], details: {}, protocol: {} } }));
    list.push({ ...reading('sensor.reading_lamp_power', 'Reading lamp Power', '4.2', 'hw-lamp', 'W', 'power', 'diagnostic'),
      original_name: 'Power' });
  } else if (id === 'hw-tv') {
    list.push(entity('media_player.living_tv', 'Living TV', s['media_player.living_tv']));
  } else if (id === 'hw-plug') {
    list.push(entity('switch.kettle_plug', 'Kettle plug', s['switch.kettle_plug']));
    list.push(reading('sensor.kettle_plug_power', 'Kettle plug Power', '0', 'hw-plug', 'W', 'power'));
    list.push(reading('sensor.kettle_plug_energy', 'Kettle plug Energy', '12.4', 'hw-plug', 'kWh', 'energy'));
    list.push(reading('sensor.kettle_plug_voltage', 'Kettle plug Voltage', 'unavailable', 'hw-plug', 'V', 'voltage'));
  } else if (id === 'hw-bulb') {
    list.push(entity('light.desk_bulb', 'Desk bulb', s['light.desk_bulb']));
    list.push(config('number.desk_bulb_default_fade', 'Desk bulb Default fade', '400',
      { min: 0, max: 5000, step: 100, mode: 'slider' }, 'ms'));
    list.push(config('select.desk_bulb_effect', 'Desk bulb Effect', 'none', { options: ['none', 'slow_pulse', 'rainbow'] }));
    list.push(config('select.desk_bulb_power_on_state', 'Desk bulb Power-on state', 'restore',
      { options: ['restore', 'always_on', 'always_off'] }));
    list.push(entity('update.desk_bulb_firmware', 'Desk bulb Firmware',
      st('update.desk_bulb_firmware', 'Firmware', 'on', 'hw-bulb', 'study', { attributes: { supported_features: 1 | 4 | 16,
        installed_version: '2026.10.1', latest_version: '2026.11.0', title: 'Bulb firmware', in_progress: false,
        release_url: 'https://example.com/releases/' } }), { entity_category: 'config', voice: QUIET }));
    list.push(reading('sensor.desk_bulb_wifi_signal', 'Desk bulb WiFi signal', '-61', 'hw-bulb', 'dBm', 'signal_strength',
      'diagnostic'));
  } else if (id === 'hw-speaker') {
    list.push(entity('media_player.kitchen_speaker', 'Kitchen speaker', s['media_player.kitchen_speaker']));
  } else if (id === 'hw-den-tv') {
    list.push(entity('media_player.den_tv', 'Den TV', s['media_player.den_tv']));
    list.push(entity('remote.den_tv', 'Den TV', s['remote.den_tv']));
  } else if (id === 'hw-vacuum') {
    list.push(entity('vacuum.robot_vacuum', 'Robot vacuum', s['vacuum.robot_vacuum']));
    list.push(entity('image.robot_vacuum_map', 'Robot vacuum Map',
      st('image.robot_vacuum_map', 'Robot vacuum Map', ago(30), 'hw-vacuum', null), { voice: QUIET }));
    list.push(reading('sensor.robot_vacuum_battery', 'Robot vacuum Battery', '87', 'hw-vacuum', '%', 'battery'));
    list.push(reading('sensor.robot_vacuum_filter_time_left', 'Robot vacuum Filter time left', '42', 'hw-vacuum', 'h', 'duration'));
    list.push(reading('sensor.robot_vacuum_main_brush_time_left', 'Robot vacuum Main brush time left', '120', 'hw-vacuum', 'h', 'duration'));
    list.push({ ...entity('sensor.robot_vacuum_vacuum_error', 'Robot vacuum Vacuum error',
      st('sensor.robot_vacuum_vacuum_error', 'Vacuum error', 'none', 'hw-vacuum', null, { device_class: 'enum' })), voice: QUIET });
  } else if (id === 'hw-camera') {
    list.push(entity('camera.porch_camera', 'Porch camera', s['camera.porch_camera']));
    list.push(entity('siren.porch_camera_siren', 'Porch camera Siren',
      st('siren.porch_camera_siren', 'Siren', 'off', 'hw-camera', 'porch', { attributes: { supported_features: 3 } })));
    list.push(entity('light.porch_camera_floodlight', 'Porch camera Floodlight',
      st('light.porch_camera_floodlight', 'Floodlight', 'off', 'hw-camera', 'porch', { attributes: { supported_color_modes: ['onoff'] } })));
  } else if (id === 'hw-thermostat') {
    list.push(entity('climate.hall_thermostat', 'Hall thermostat', s['climate.hall_thermostat']));
  } else if (id === 'hw-garage') {
    list.push(entity('cover.garage_door', 'Garage door', s['cover.garage_door']));
  } else if (id === 'hw-lock') {
    list.push(entity('lock.front_door', 'Front door lock', s['lock.front_door']));
    list.push(reading('sensor.front_door_lock_battery', 'Front door lock Battery', '64', 'hw-lock', '%', 'battery', 'diagnostic'));
  } else if (id === 'hw-fan') {
    list.push(entity('fan.study_fan', 'Study fan', s['fan.study_fan']));
  } else if (id === 'hw-sensor') {
    list.push(entity('sensor.hall_temperature', 'Hall climate sensor Temperature', s['sensor.hall_temperature'], { voice: QUIET }));
    list.push(reading('sensor.hall_humidity', 'Hall climate sensor Humidity', '44', 'hw-sensor', '%', 'humidity'));
    list.push(reading('sensor.hall_battery', 'Hall climate sensor Battery', '87', 'hw-sensor', '%', 'battery', 'diagnostic'));
  }
  return {
    ...base, children: [], entry: null,
    entity_list: list.map((e) => (voices.has(e.entity_id)
      ? { ...e, voice: { ...e.voice, exposed: voices.get(e.entity_id)!, chosen: true } } : e)),
  };
}

/** The lamp's detail, for specs that do not need the live states. */
export const HARDWARE_DETAIL = detailFor('hw-lamp', initialStates());

/** A temperature over the last day, for the sensor's small chart. */
function history(entityIds: string[]) {
  const series: Record<string, { t: string; s: string }[]> = {};
  for (const id of entityIds) {
    if (id === 'sensor.hall_temperature') {
      series[id] = Array.from({ length: 25 }, (_, i) => ({ t: ago((24 - i) * 60), s: (20 + 1.5 * Math.sin(i / 3.8)).toFixed(1) }));
    }
    if (id === 'sensor.kettle_plug_power') {
      series[id] = Array.from({ length: 25 }, (_, i) => ({ t: ago((24 - i) * 60), s: String([7, 8, 19].includes(i) ? 1800 : 0) }));
    }
  }
  return { series };
}

const MAP_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200"><rect width="320" height="200" fill="#0b1220"/>
<g fill="#1e3a5f" stroke="#38bdf8" stroke-width="2"><rect x="20" y="20" width="130" height="90" rx="4"/><rect x="150" y="20" width="150" height="60" rx="4"/>
<rect x="150" y="80" width="80" height="100" rx="4"/><rect x="20" y="110" width="130" height="70" rx="4"/></g>
<path d="M40 60 L120 60 L120 90 L180 90 L200 140" fill="none" stroke="#a7f3d0" stroke-width="2" stroke-dasharray="4 3"/>
<circle cx="200" cy="140" r="6" fill="#34d399"/><rect x="262" y="150" width="14" height="10" fill="#fbbf24"/></svg>`;

const PORCH_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"><defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#4b6584"/><stop offset="1" stop-color="#a5b1c2"/></linearGradient></defs><rect width="320" height="180" fill="url(#s)"/>
<rect y="120" width="320" height="60" fill="#57606f"/><rect x="40" y="50" width="120" height="80" fill="#8e735b"/><rect x="85" y="80" width="30" height="50" fill="#3d3d3d"/>
<rect x="190" y="95" width="90" height="10" fill="#2f3542"/><circle cx="270" cy="40" r="14" fill="#f5f6fa" opacity=".7"/></svg>`;

export interface DeviceStateMocks {
  states: States;
  commands: { domain: string; service: string; entity_id: string; service_data: Record<string, unknown> | null }[];
}

/**
 * Live states for the Devices specs, kept in memory: a command changes them, so the next read
 * shows it. The kettle plug refuses every command, the way execution says so (a FAILURE body
 * with HTTP 200). Register after mockHome, so these answer first.
 */
export async function mockDeviceStates(page: Page, voicesFrom: () => Map<string, boolean> = () => new Map()): Promise<DeviceStateMocks> {
  const states = initialStates();
  const commands: DeviceStateMocks['commands'] = [];
  await page.route('**/api/dashboard/rooms', (route) => route.fulfill({ json: rooms(states) }));
  await page.route('**/api/dashboard/history**', (route) => {
    const url = new URL(route.request().url());
    return route.fulfill({ json: history((url.searchParams.get('entity_ids') || '').split(',')) });
  });
  await page.route(/\/api\/homehub\/hardware\/[^/?]+(\?.*)?$/, (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() || '');
    return route.fulfill({ json: detailFor(id, states, voicesFrom()) });
  });
  // Pictures that look like what they stand for in screenshots: a drawn floor plan and a drawn porch.
  await page.route('**/api/media/images/**', (route) => route.fulfill({ contentType: 'image/svg+xml', body: MAP_SVG }));
  await page.route('**/api/media/cameras/**', (route) => route.fulfill({ contentType: 'image/svg+xml', body: PORCH_SVG }));
  await page.route('**/execute/ha_service', (route) => {
    const body = route.request().postDataJSON() as DeviceStateMocks['commands'][number];
    commands.push(body);
    if (body.entity_id === 'switch.kettle_plug') {
      return route.fulfill({ json: { status: 'FAILURE', message: 'Kettle plug did not answer. Check that it is plugged in.' } });
    }
    const e = states[body.entity_id];
    if (e) {
      const data = body.service_data ?? {};
      if (body.service === 'turn_off') e.state = 'off';
      if (body.service === 'turn_on') {
        e.state = 'on';
        const attrs = { ...e.attributes };
        if (data.brightness_pct != null) attrs.brightness = Math.round((Number(data.brightness_pct) / 100) * 255);
        if (data.color_temp_kelvin != null) { attrs.color_temp_kelvin = data.color_temp_kelvin; attrs.color_mode = 'color_temp'; }
        if (data.rgb_color != null) { attrs.rgb_color = data.rgb_color; attrs.color_mode = 'rgb'; }
        e.attributes = attrs;
      }
      if (body.service === 'media_pause') e.state = 'paused';
      if (body.service === 'media_play') e.state = 'playing';
      if (body.service === 'volume_set') e.attributes = { ...e.attributes, volume_level: data.volume_level };
      if (body.service === 'set_hvac_mode') e.state = String(data.hvac_mode);
      if (body.service === 'set_temperature') e.attributes = { ...e.attributes, ...data };
      if (body.service === 'set_fan_speed') e.attributes = { ...e.attributes, fan_speed: data.fan_speed };
      if (body.service === 'start') e.state = 'cleaning';
      if (body.service === 'lock') e.state = 'locked';
      if (body.service === 'unlock') e.state = 'unlocked';
      if (body.service === 'open_cover') e.state = 'opening';
      if (body.service === 'set_percentage') e.attributes = { ...e.attributes, percentage: data.percentage };
    }
    return route.fulfill({ json: { status: 'SUCCESS', message: 'Done.' } });
  });
  return { states, commands };
}

export const MOVED_FINDING = {
  id: 'roku:x00000000001', integration: 'roku', label: 'Roku', name: 'Living TV', model: null, manufacturer: null,
  host: '192.0.2.96', mac: null, source: 'ssdp', first_seen: ago(600), last_seen: ago(2), known: true, entry_id: 'e-roku',
  entry_title: 'Living TV', ignored: false, new: false, adoptable: false, unsupported: null, needs: [],
  moved: { entry_id: 'e-roku', entry_title: 'Living TV', old_host: '192.0.2.66', new_host: '192.0.2.96' },
};

export const FLOWS = [
  { domain: 'roku', label: 'Roku', kind: 'local', fields: [{ key: 'host', label: 'Address', type: 'text' }] },
  { domain: 'roborock', label: 'Roborock account', kind: 'cloud', fields: [{ key: 'email', label: 'Email', type: 'text' }] },
  // App vetting's hub account (docs/APP_VETTING.md), as services/homehub/manual_flows.py lists it. The abuse.ch
  // key is not here: it is in Accounts & Integrations.
  { domain: 'common_sense_media', label: 'Common Sense Media (app age ratings)', kind: 'cloud', single: true,
    configured: false, fields: [] },
  { domain: 'switch_as_x', label: 'Switch as another device', kind: 'helper', fields: [
    { key: 'entity_id', label: 'Switch', type: 'select', options: [{ value: 'switch.kettle', label: 'Kettle plug' }] },
    { key: 'target_domain', label: 'Show it as', type: 'select', options: [{ value: 'light', label: 'Light' }] }] },
];
