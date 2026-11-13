import type { Page, Route } from '@playwright/test';
import { isServerCall } from './serverCalls';
import { FLOWS, HARDWARE, HARDWARE_DETAIL, MOVED_FINDING } from './hardwareMocks';

/**
 * A made-up house for the home pages (Rooms, Climate, Cameras, Energy, History,
 * Automations, Schedules, Devices), answered entirely in the browser. Names and
 * ids are invented; nothing here is a real household. The catch-all answers
 * anything else with an empty list and records it, so a page that starts
 * calling something new shows up in `unmocked` instead of hanging.
 */

export type Role = 'admin' | 'member';

export const PROFILES: Record<Role, Record<string, unknown>> = {
  admin: { id: 1, username: 'parent', display_name: 'Parent', full_name: 'Parent', role: 'admin', is_admin: true, family_role: 'father', is_minor: false },
  member: { id: 2, username: 'kid', display_name: 'Kid', full_name: 'Kid', role: 'user', is_admin: false, family_role: 'child', is_minor: true },
};

const NOW = new Date();
const iso = (offsetMin = 0) => new Date(NOW.getTime() + offsetMin * 60_000).toISOString();

const ent = (entity_id: string, name: string, state: string, extra: Record<string, unknown> = {}) => ({
  entity_id, domain: entity_id.split('.')[0], name, state, attributes: {}, device_class: null, unit: null, icon: null,
  area_id: null, device_id: null, last_changed: iso(-30), ...extra,
});

const THERMOSTAT_ATTRS = {
  hvac_modes: ['off', 'heat', 'cool', 'heat_cool'], hvac_action: 'cooling', current_temperature: 76, temperature: 74,
  min_temp: 60, max_temp: 90, target_temp_step: 1,
};

export const ROOMS = {
  floors: [{ floor_id: 'ground', name: 'Ground floor', level: 0 }],
  areas: [
    { area_id: 'living', name: 'Living room', floor_id: 'ground', icon: null, entities: [
      ent('light.living_lamp', 'Reading lamp', 'on', { attributes: { brightness: 180, supported_color_modes: ['brightness'] } }),
      ent('light.living_ceiling', 'Ceiling light', 'on'),
      ent('switch.living_fan', 'Ceiling fan', 'off'),
      ent('sensor.living_temperature', 'Temperature', '74.2', { unit: '°F', device_class: 'temperature' }),
      ent('climate.hallway', 'Downstairs', 'cool', { attributes: THERMOSTAT_ATTRS }),
    ] },
    { area_id: 'kitchen', name: 'Kitchen with a rather long room name for small phones', floor_id: 'ground', icon: null, entities: [
      ent('light.kitchen_pendants', 'Pendants', 'off'),
      ent('light.kitchen_sink', 'Sink light', 'unavailable', { offline: { cause: 'device', integration: 'Matter' } }),
      ent('binary_sensor.back_door', 'Back door', 'off', { device_class: 'door' }),
      ent('camera.yard', 'Yard', 'idle'),
    ] },
    { area_id: 'bedroom', name: 'Bedroom', floor_id: null, icon: null, entities: [
      ent('light.bedroom_lamp', 'Bedside lamp', 'on'),
    ] },
  ],
  unassigned: [ent('vacuum.robot', 'Robot', 'docked')],
};

const DASH_THERMOSTAT = {
  ...ent('climate.hallway', 'Downstairs', 'cool', { attributes: THERMOSTAT_ATTRS }),
  area_name: 'Living room', power: [ent('sensor.ac_power', 'AC power', '2.4', { unit: 'kW', device_class: 'power' })],
  power_source: 'area', sensors: [ent('sensor.living_humidity', 'Humidity', '41', { unit: '%', device_class: 'humidity' })],
};

const SCHEDULED_THERMOSTAT = {
  entity_id: 'climate.hallway', name: 'Downstairs', area: 'Living room', state: 'cool',
  current_temperature: 76, temperature: 74, hvac_action: 'cooling',
  capabilities: { hvac_modes: ['off', 'heat', 'cool', 'heat_cool'], fan_modes: ['auto'], min_temp: 60, max_temp: 90, unit: '°F' },
  current: { program_id: 'p1', program_name: 'Weekdays', slot: 0, start: '06:00', end: '17:00', setpoint: { hvac_mode: 'cool', temperature: 74 } },
  next: { program_id: 'p1', program_name: 'Weekdays', slot: 1, at: iso(180), setpoint: { hvac_mode: 'cool', temperature: 78 } },
  hold: null, effective: { source: 'schedule', setpoint: { hvac_mode: 'cool', temperature: 74 } },
  summary: 'Following Weekdays: 74, goes to 78 at 5 PM',
};

const PROGRAM = {
  id: 'p1', name: 'Weekdays', entity_ids: ['climate.hallway'], weekdays: ['workday'], start_date: null, end_date: null,
  enabled: true, slots: [
    { start: '06:00', hvac_mode: 'cool', temperature: 74 },
    { start: '17:00', hvac_mode: 'cool', temperature: 78 },
    { start: '22:00', hvac_mode: 'cool', temperature: 72 },
  ],
};

const CLIMATE_SETTINGS = {
  away: { enabled: false, start: null, end: null, auto: false, persons: [], setpoint: { hvac_mode: 'cool', temperature: 82 }, per_entity: {}, thermostats: [] },
  energy: { enabled: false, power_sensor: null, windows: [], shed_after_min: 10, resume_below_kw: null, comfort_max_temp: null,
    comfort_setpoint: null, thermostats: [] },
};

function climateOverview(role: Role) {
  return {
    thermostats: [SCHEDULED_THERMOSTAT], programs: [PROGRAM], settings: CLIMATE_SETTINGS,
    away: { active: false, reason: null }, energy: { in_window: false, window: null, power_kw: null, shed: [] },
    can_edit: role === 'admin', now: iso(), time_zone: 'UTC',
  };
}

const ENERGY_SUMMARY = {
  start: iso(-600), end: iso(), period: 'hour', currency: 'USD',
  grid: { kwh: 18.4, series: Array.from({ length: 10 }, (_, i) => ({ start: iso(-600 + i * 60), value: 1 + (i % 4) / 2 })),
    returned_kwh: 0, cost: 2.31, cost_source: 'flat_price', power_w: 2400, stats: ['sensor.house_energy'] },
  solar: null, tou: { source: null, periods: [] },
  devices: [{ name: 'Air conditioner', stat: 'sensor.ac_energy', kwh: 9.1, series: [], power_entity: 'sensor.ac_power', power_w: 2400 }],
  untracked_kwh: 9.3, utility: [],
};

const ENERGY_PREFS = {
  energy_sources: [{ type: 'grid', stat_energy_from: 'sensor.house_energy' }], device_consumption: [{ stat_consumption: 'sensor.ac_energy' }],
  utility_sources: [], tariff: { currency: 'USD', flat_price: 0.13, periods: [], default_period: { name: 'Off-peak', price: null } },
};

const CAMERAS = [
  { entity_id: 'camera.yard', name: 'Yard', area: 'Kitchen', platform: 'reolink', state: 'idle', recordings: true, live: ['mjpeg'], stream: 'camera' },
  { entity_id: 'camera.porch', name: 'Porch', area: 'Front', platform: 'reolink', state: 'idle', recordings: false, live: ['mjpeg'], stream: 'camera', battery: true, rotation: 90 },
];

const AUTOMATION = {
  kind: 'automation', id: 'a1', entity_id: 'automation.porch_at_dusk', alias: 'Porch light at dusk', description: null, icon: null,
  enabled: true, runnable: true, unsupported_reason: null, warnings: [], last_triggered: iso(-600), last_error: null,
  mode: 'single', running: 0, entities: ['light.living_lamp'],
};

const DISCOVERED = {
  devices: [{
    id: 'esphome:aa01', integration: 'esphome', label: 'ESPHome', name: 'Garage sensor', model: null, manufacturer: null,
    host: '192.0.2.40', mac: null, source: 'zeroconf', first_seen: iso(-60), last_seen: iso(-1), known: false, entry_id: null,
    entry_title: null, ignored: false, new: true, adoptable: true, unsupported: null, needs: [],
  }],
  last_scan: { at: iso(-5) },
};

const ENTRIES = {
  areas: ROOMS.areas.map((a) => ({ area_id: a.area_id, name: a.name })),
  entries: [
    { entry_id: 'e1', domain: 'reolink', label: 'Reolink', title: 'Camera hub', state: 'loaded', reason: null, source: 'user',
      disabled_by: null, entities: 4, unpairs: false, shared: false, reconfigure: [],
      devices: [{ id: 'd1', name: 'Yard', original_name: 'Yard', name_by_user: null, area_id: 'kitchen', manufacturer: 'Example',
        model: 'Cam', via_device_id: null, entities: ['camera.yard'] }] },
    { entry_id: 'e2', domain: 'music_assistant', label: 'Music Assistant', title: 'Music server', state: 'setup_retry',
      reason: 'Could not connect', source: 'user', disabled_by: null, entities: 0, unpairs: false, shared: false, reconfigure: [], devices: [] },
  ],
};

const phoneSensor = (entity_id: string, name: string, state: string, unit: string | null = null,
  device_class: string | null = null) => ({
  entity_id, name, domain: entity_id.split('.')[0], state, unit, device_class, disabled: false, source: 'jarvis',
  last_updated: null,
});

const PHONES = {
  phones: [{
    entry_id: 'phone-1', name: "Robin's Phone", kind: 'jarvis', app: {}, jarvis_user: 'robin', jarvis_fallback: true,
    companion_last_seen: null, companion_fresh: false, encrypted: false, push: { relay: false, local_channel: false },
    notify_service: 'notify.mobile_app_robin_s_phone', state: 'loaded',
    sensors: [
      phoneSensor('sensor.robin_s_phone_battery_level', "Robin's Phone Battery level", '64', '%', 'battery'),
      phoneSensor('sensor.robin_s_phone_battery_state', "Robin's Phone Battery state", 'charging'),
      phoneSensor('sensor.robin_s_phone_charger_type', "Robin's Phone Charger type", 'wireless'),
      phoneSensor('binary_sensor.robin_s_phone_is_charging', "Robin's Phone Is charging", 'on', null, 'plug'),
      phoneSensor('sensor.robin_s_phone_next_alarm', "Robin's Phone Next alarm", '2030-03-18T13:30:00+00:00', null,
        'timestamp'),
      { ...phoneSensor('sensor.robin_s_phone_room', "Robin's Phone Room", 'Bedroom'), last_updated: iso(-1) },
    ],
    beacon: { state: 'advertising', reason: null, uuid: '00000000-0000-4000-8000-0000000000aa', major: 1, minor: 2, tx_power: -59,
      tx_power_level: 'low', reported_at: iso(-5), hub_registered: true, hub_detail: null },
  }],
  settings: { external_url: '', internal_url: '', companion_fresh_minutes: 10, jarvis_fallback: true, auto_jarvis_phones: true,
    jarvis_phone_name: '{user} phone', push_url_allowlist: [] },
};

/** The hub's Bluetooth room map (Devices > Room detection). */
export const ROOM_MAP = {
  method: 'fingerprint', scanners: [{ id: 'esp-den', name: 'Den listener' }], overall_accuracy: 0.81,
  stale_reason: 'A listener was added after some rooms were recorded.',
  confusion: { living: { living: 80, kitchen: 9 }, kitchen: { kitchen: 30, living: 5 } },
  areas: [
    { area_id: 'living', name: 'Living room', samples: 89, sessions: 2, last_recorded: iso(-60 * 24), accuracy: 0.9 },
    { area_id: 'kitchen', name: 'Kitchen with a rather long room name for small phones', samples: 35, sessions: 1,
      last_recorded: iso(-60 * 48), accuracy: 0.62 },
  ],
};

function statsStatus() {
  return {
    metadata: 42, statistics: { rows: 1000, first: iso(-60 * 24 * 400), last: iso(-60) },
    statistics_short_term: { rows: 100, first: iso(-60 * 24 * 10), last: iso(-5) }, last_import: { imported_at: iso(-60 * 24) }, last_error: null,
  };
}

export interface HomeMocks {
  unmocked: string[];
  posts: Array<{ path: string; body: unknown }>;
}

/** Answer every server call for the home pages as `role`. */
export async function mockHome(page: Page, role: Role): Promise<HomeMocks> {
  const unmocked: string[] = [];
  const posts: HomeMocks['posts'] = [];
  const profile = PROFILES[role];
  // Camera turns an admin sets, so the list read after a rotate says the new one.
  const rotations = new Map<string, number>();
  await page.addInitScript((p) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(p));
  }, profile);

  await page.route(isServerCall, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const p = url.pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (method !== 'GET') {
      let body: unknown;
      try { body = request.postDataJSON(); } catch { body = null; }
      posts.push({ path: p, body });
      if (p === '/api/homehub/climate/hold' || p === '/api/homehub/climate/resume') return json(SCHEDULED_THERMOSTAT);
      // Minted with a POST; without it no camera picture is ever signed.
      if (p === '/api/media/token') return json({ token: 'test-media-token', expires_at: 4102444800, user: profile.username });
      const entityPatch = /^\/api\/homehub\/entities\/([^/]+)$/.exec(p);
      if (method === 'PATCH' && entityPatch && typeof (body as { rotation?: unknown })?.rotation === 'number') {
        rotations.set(decodeURIComponent(entityPatch[1]), (body as { rotation: number }).rotation);
      }
      if (p === '/api/homehub/bluetooth/map/sessions') {
        return json({ session_id: 'map-1', area_id: 'bedroom', tracker: 'sensor.robin_s_phone', ends_at: iso(1) });
      }
      return json({ status: 'ok' });
    }

    // The shell's own calls.
    if (p === '/api/users/me') return json(profile);
    if (p === '/api/users/me/theme') return json({ theme: 'dark' });
    if (p === '/api/telemetry/notifications') return json({ notifications: [] });
    if (p === '/api/communication/calls') return json({ calls: [] });
    if (p === '/api/media/token') return json({ token: 'test-media-token', expires_at: 4102444800, user: profile.username });
    if (p === '/api/health') return json({ status: 'ok', services: {} });
    if (p === '/api/admin/services/updates') return json({ checked: 0, updates_available: 0, services: [] });
    if (p === '/api/users/family') return json([]);
    if (p === '/api/info') return json({ service: 'jarvis-ui', version: 'e2e', git_sha: 'e2e' });

    // The home pages.
    if (p === '/api/dashboard/rooms') return json(ROOMS);
    if (p === '/api/dashboard/climate') return json({ thermostats: [DASH_THERMOSTAT] });
    if (p === '/api/dashboard/history') return json({ series: {} });
    if (p === '/api/dashboard/statistics') return json({ statistics: {} });
    if (p === '/api/dashboard/statistics/status') return json(statsStatus());
    if (p === '/api/dashboard/entities') {
      return json({ entities: [
        { entity_id: 'sensor.living_temperature', name: 'Living room temperature', domain: 'sensor', unit: '°F', device_class: 'temperature', state: '74' },
        { entity_id: 'climate.hallway', name: 'Downstairs', domain: 'climate', unit: null, device_class: null, state: 'cool' },
      ] });
    }
    if (p === '/api/dashboard/energy/summary') return json(ENERGY_SUMMARY);
    if (p === '/api/dashboard/energy/prefs') return json(ENERGY_PREFS);
    if (p === '/api/dashboard/energy/candidates') return json([]);
    if (p === '/api/dashboard/prefs') return json({ climate_power: {}, hidden: [] });
    if (p === '/api/homehub/climate') return json(climateOverview(role));
    if (p === '/api/homehub/schedules') return json({ schedules: [], can_edit: role === 'admin' });
    if (p === '/api/homehub/automations') return json({ items: [AUTOMATION], can_edit: role === 'admin' });
    if (p === '/api/homehub/automations/catalog') {
      return json({ triggers: [], conditions: [], actions: [], services: [], entities: [], areas: [], zones: [], persons: [] });
    }
    if (p === '/api/homehub/automations/automation/a1') {
      return json({ item: AUTOMATION, config: { alias: AUTOMATION.alias, triggers: [], conditions: [], actions: [] },
        yaml: 'alias: Porch light at dusk\ntriggers: []\nactions: []\n', can_edit: role === 'admin' });
    }
    if (p === '/api/homehub/automations/remap') return json({ remap: {}, report: [] });
    if (p === '/api/cameras') {
      return json({ cameras: CAMERAS.map((c) => (rotations.has(c.entity_id) ? { ...c, rotation: rotations.get(c.entity_id) } : c)) });
    }
    if (/^\/api\/cameras\/[^/]+\/recordings$/.test(p)) {
      return json({ recordings: [{ start: iso(-120), end: iso(-119), trigger: 'person', id: 'r1' }] });
    }
    if (p === '/api/admin/cameras/live-video') return json({ configured: false, cameras: [], default_quality: 'sub' });
    if (p === '/api/homehub/discovered') return json({ ...DISCOVERED, devices: [...DISCOVERED.devices, MOVED_FINDING] });
    if (p === '/api/homehub/hardware') return json(HARDWARE);
    if (p.startsWith('/api/homehub/hardware/')) return json(HARDWARE_DETAIL);
    if (p === '/api/homehub/entries') return json(ENTRIES);
    if (p === '/api/homehub/esphome-builder') return json({ configured: false });
    if (p === '/api/homehub/flows') return json({ flows: FLOWS });
    if (p === '/api/homehub/phones') return json(PHONES);
    if (p === '/api/homehub/phones/settings') return json(PHONES.settings);
    if (p === '/api/homehub/bluetooth/map') return json(ROOM_MAP);
    if (p.startsWith('/api/homehub/bluetooth/map/sessions/')) {
      return json({ status: 'done', samples: 58, scanners_heard: 3, seconds_left: 0 });
    }
    if (p === '/api/mdm/devices') return json({ devices: [], members: [] });
    if (p === '/api/mdm/files') return json({ folders: [] });
    if (p === '/api/mdm/audit') return json({ events: [] });

    unmocked.push(`${method} ${p}`);
    return route.fulfill({ json: [] });
  });
  // Camera pictures: a 1x1 image instead of a network error.
  await page.route('**/api/media/cameras/**', (route) =>
    route.fulfill({ contentType: 'image/png', body: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64') }));
  return { unmocked, posts };
}
