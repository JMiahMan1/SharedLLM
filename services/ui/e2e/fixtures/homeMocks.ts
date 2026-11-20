import type { Page, Route } from '@playwright/test';
import { isServerCall, INTEGRATIONS_STATUS_PATH, integrationsStatus } from './serverCalls';
import { FLOWS, HARDWARE, MOVED_FINDING, detailFor, initialStates } from './hardwareMocks';
import { TINY_WEBM_BASE64 } from './tinyVideo';
import { registryMocks } from './registryMocks';

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

const cam = (entity_id: string, name: string, state: string, group: string, role: string, extra: Record<string, unknown> = {}) => ({
  entity_id, name, state, group, role, domain: entity_id.split('.')[0], attributes: {}, device_class: null, unit: null,
  icon: null, area_id: null, device_id: 'd-yard', last_changed: iso(-60), ...extra,
});

/** The Yard camera's device: a gate opener, a siren, detections, battery, and settings. */
const YARD_CONTROLS = {
  entity_id: 'camera.yard',
  actions: [
    cam('switch.yard_gate', 'Yard Gate', 'off', 'actions', 'opener',
      { door: cam('binary_sensor.yard_gate_sensor', 'Yard Gate Sensor', 'off', 'status', 'door', { device_class: 'door' }) }),
    cam('siren.yard_siren', 'Yard Siren', 'unknown', 'actions', 'siren'),
    cam('light.yard_floodlight', 'Yard Floodlight With A Long Name For Small Phones', 'off', 'actions', 'light'),
  ],
  status: [
    cam('binary_sensor.yard_motion', 'Yard Motion', 'on', 'status', 'motion', { device_class: 'motion' }),
    cam('binary_sensor.yard_person', 'Yard Person', 'off', 'status', 'person'),
    cam('sensor.yard_battery', 'Yard Battery', '15', 'status', 'battery', { device_class: 'battery', unit: '%' }),
  ],
  settings: [
    cam('number.yard_volume', 'Yard Volume', '90', 'settings', 'number', { attributes: { min: 0, max: 100, step: 1 } }),
    cam('select.yard_day_night_mode', 'Yard Day Night Mode', 'auto', 'settings', 'select',
      { attributes: { options: ['auto', 'color', 'black_and_white'] } }),
    cam('switch.yard_siren_on_event', 'Yard Siren On Event', 'off', 'settings', 'switch'),
  ],
};

const RECORDING_DEFAULTS = {
  enabled: false, sensitivity: 60, pre_s: 3, post_s: 10, max_s: 120, fps: 4, retention_days: 14, schedule: null,
};

const MOTION_RECORDING = {
  configured: false, enabled: false, state: null, reason: null, account: null,
  household_account: { url: 'https://cloud.example.test', username: 'household' },
  url: '', username: '', has_password: false, folder: 'Jarvis/Cameras', defaults: RECORDING_DEFAULTS,
  limits: { sensitivity: [1, 100], pre_s: [0, 10], post_s: [2, 60], max_s: [10, 600], fps: [1, 10], retention_days: [1, 365] },
  error: null,
  cameras: [
    { entity_id: 'camera.shed', name: 'Shed', platform: 'esphome', records_itself: false, unavailable_reason: null,
      settings: RECORDING_DEFAULTS, motion_entity: null, status: null },
    { entity_id: 'camera.porch', name: 'Porch', platform: 'reolink', records_itself: false,
      unavailable_reason: 'it runs on a battery, and every picture would wake it', settings: RECORDING_DEFAULTS,
      motion_entity: null, status: null },
    { entity_id: 'camera.yard', name: 'Yard', platform: 'reolink', records_itself: true, unavailable_reason: null,
      settings: RECORDING_DEFAULTS, motion_entity: null, status: null },
  ],
};

export const AUTOMATION = {
  kind: 'automation', id: 'a1', entity_id: 'automation.porch_at_dusk', alias: 'Porch light at dusk', description: null, icon: null,
  enabled: true, runnable: true, unsupported_reason: null, warnings: [], last_triggered: iso(-600), last_error: null,
  mode: 'single', running: 0, entities: ['light.living_lamp'],
};

// Helpers the hub keeps (made-up names), and the types it describes.
const HELPER_TYPES = [
  { domain: 'input_boolean', label: 'Toggle', services: ['toggle', 'turn_off', 'turn_on'],
    fields: [{ name: 'initial', label: 'Starts as on', kind: 'boolean' }] },
  { domain: 'input_select', label: 'Dropdown', services: ['select_option'],
    fields: [{ name: 'options', label: 'Options', kind: 'options', required: true }, { name: 'initial', label: 'Starting option', kind: 'text' }] },
  { domain: 'counter', label: 'Counter', services: ['decrement', 'increment', 'reset', 'set_value'],
    fields: [{ name: 'initial', label: 'Starting value', kind: 'number', default: 0 }, { name: 'step', label: 'Step', kind: 'number', default: 1 }] },
  { domain: 'timer', label: 'Timer', services: ['cancel', 'change', 'finish', 'pause', 'start'],
    fields: [{ name: 'duration', label: 'Duration (HH:MM:SS)', kind: 'duration', default: '0:00:00' },
      { name: 'restore', label: 'Keep running across restarts', kind: 'boolean', default: false }] },
];

function helperRow(domain: string, id: string, name: string, state: string, attributes: Record<string, unknown> = {},
  config: Record<string, unknown> = {}) {
  return { domain, id, entity_id: `${domain}.${id}`, name, config: { id, name, ...config }, disabled: false,
    state: { entity_id: `${domain}.${id}`, state, attributes } };
}

function initialHelpers() {
  return [
    helperRow('input_boolean', 'guest_mode', 'Guest mode', 'off'),
    helperRow('counter', 'glasses_of_water_today_for_everyone', 'Glasses of water today for everyone in the house', '3',
      { initial: 0, step: 1 }),
    helperRow('timer', 'pasta', 'Pasta', 'idle', { duration: '0:10:00' }, { duration: '0:10:00' }),
  ];
}

const BLUEPRINT = {
  path: 'someone/motion_light.yaml', domain: 'automation', name: 'Motion-activated light with a long name for small screens',
  description: 'Turn a light on when motion is seen, and off a while after it stops.',
  inputs: [
    { name: 'motion', label: 'Motion sensor', description: '', selector: { entity: { domain: 'binary_sensor' } }, section: null, required: true },
    { name: 'light', label: 'Light', description: '', selector: { entity: { domain: 'light' } }, section: null, required: true },
    { name: 'wait', label: 'Wait after the last motion', description: 'Seconds', selector: { number: { min: 0, max: 3600 } },
      section: 'Timing', required: false, default: 120 },
  ],
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
      reason: 'Could not connect', source: 'user', disabled_by: null, offline_since: iso(-3 * 24 * 60), entities: 0, unpairs: false,
      shared: false, devices: [], reconfigure: [
        { key: 'url', label: 'Server URL', type: 'text', value: 'http://192.0.2.40:8095',
          help: 'Where Music Assistant runs, e.g. http://<server>:8095' },
        { key: 'token', label: 'Token', type: 'password', set: true },
        { key: 'mode', label: 'Connection', type: 'select', value: '',
          options: [{ value: '', label: 'Automatic' }, { value: 'ws', label: 'WebSocket only' }] },
      ] },
    // A long unbroken name, to check the offline list on a small phone.
    { entry_id: 'e3', domain: 'esphome', label: 'ESPHome', title: 'string-lights-backyard-pergola-kauf-plug-2', state: 'setup_retry',
      reason: 'DeviceUnavailable: unreachable', source: 'user', disabled_by: null, offline_since: iso(-5 * 60), entities: 1,
      unpairs: false, shared: false, reconfigure: [], devices: [] },
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

const TODO_FEATURES = { create: true, delete: true, update: true, move: true, due_date: false, due_datetime: false,
  description: false };

/** The hub's to-do lists (Lists page): made-up names, one with due dates and a long item for small phones. */
function todoLists() {
  return [
    { entity_id: 'todo.reminders', name: 'Reminders', open: 2, available: true,
      features: { ...TODO_FEATURES, due_date: true, due_datetime: true, description: true }, items: [
        { uid: 'r1', summary: 'Book the boiler service', status: 'needs_action', due: iso(-60 * 24).slice(0, 10), description: null },
        { uid: 'r2', summary: 'Return the library books before the end of the month please', status: 'needs_action',
          due: null, description: 'Three of them' },
      ] },
    { entity_id: 'todo.shopping_list', name: 'Shopping List', open: 2, available: true, features: TODO_FEATURES, items: [
      { uid: 's1', summary: 'Milk', status: 'needs_action', due: null, description: null },
      { uid: 's2', summary: 'Bread', status: 'needs_action', due: null, description: null },
      { uid: 's3', summary: 'Apples', status: 'completed', due: null, description: null },
    ] },
  ];
}

/** The Logbook tab: a few changes today and yesterday, one with a long name. */
function logbook(areaId: string | null) {
  const rows = [
    { when: iso(-5), entity_id: 'light.living_lamp', name: 'Reading lamp', state: 'on' },
    { when: iso(-40), entity_id: 'binary_sensor.back_door', name: 'Back door', state: 'off' },
    { when: iso(-90), entity_id: 'sensor.living_temperature', name: 'Living room temperature sensor with a long name', state: '74.2' },
    { when: iso(-60 * 26), entity_id: 'light.kitchen_pendants', name: 'Pendants', state: 'off' },
  ];
  const inArea = areaId ? new Set(ROOMS.areas.find((a) => a.area_id === areaId)?.entities.map((e) => e.entity_id) ?? []) : null;
  return { entries: inArea ? rows.filter((r) => inArea.has(r.entity_id)) : rows, truncated: false };
}

export interface HomeMocks {
  unmocked: string[];
  posts: Array<{ path: string; body: unknown }>;
}

/** A room added, renamed or removed through the hub's area routes also changes what the Rooms page reads. */
function mirrorRoomWrite(rooms: typeof ROOMS, method: string, path: string, body: unknown,
  answer: { status: number; body: unknown }): void {
  const m = /^\/api\/homehub\/areas(?:\/([^/]+))?$/.exec(path);
  if (!m || answer.status !== 200) return;
  const id = m[1] ? decodeURIComponent(m[1]) : null;
  const sent = (body ?? {}) as { name?: string; floor_id?: string | null };
  if (method === 'POST' && !id) {
    const row = answer.body as { area_id: string; name: string; floor_id?: string | null };
    rooms.areas.push({ area_id: row.area_id, name: row.name, floor_id: row.floor_id ?? null, icon: null, entities: [] });
    return;
  }
  const at = rooms.areas.findIndex((a) => a.area_id === id);
  if (at < 0) return;
  if (method === 'DELETE') rooms.areas.splice(at, 1);
  if (method === 'PATCH') rooms.areas[at] = { ...rooms.areas[at], ...sent } as (typeof ROOMS)['areas'][number];
}

export interface HomeOptions {
  /** What the Rooms page reads (default ROOMS); `{ floors: [], areas: [], unassigned: [] }` is a home with no rooms. */
  rooms?: typeof ROOMS;
  /** What the Automations page lists (default: one ordinary automation). */
  automations?: Array<Record<string, unknown>>;
}

/** Answer every server call for the home pages as `role`. */
export async function mockHome(page: Page, role: Role, options: HomeOptions = {}): Promise<HomeMocks> {
  const unmocked: string[] = [];
  // The Rooms page's answer, changed by room writes so a room added, renamed or removed shows on the next read.
  const rooms = structuredClone(options.rooms ?? ROOMS);
  const posts: HomeMocks['posts'] = [];
  const profile = PROFILES[role];
  // Camera turns an admin sets, so the list read after a rotate says the new one.
  const rotations = new Map<string, number>();
  // "Voice can control" choices made in a device sheet, so the sheet read after a toggle shows them.
  const voices = new Map<string, boolean>();
  // Device locks set from a camera's Who can watch, so the next read shows them.
  const locks = new Map<string, string[]>();
  // Areas, floors, labels, zones and persons: kept in memory so a change shows on the next read.
  const registries = registryMocks();
  // Helpers an admin adds, changes or deletes, so the next read shows it.
  let helpers = initialHelpers();
  // The to-do lists, changed by the page's writes so the next read shows them.
  const lists = todoLists();
  const listOf = (id: string) => lists.find((l) => l.entity_id === id);
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
    if (p === INTEGRATIONS_STATUS_PATH) return route.fulfill({ json: integrationsStatus() });
    if (method !== 'GET') {
      let body: unknown;
      try { body = request.postDataJSON(); } catch { body = null; }
      posts.push({ path: p, body });
      const registry = registries.answer(method, p, body);
      if (registry) {
        mirrorRoomWrite(rooms, method, p, body, registry);
        return json(registry.body, registry.status);
      }
      if (p === '/api/homehub/climate/hold' || p === '/api/homehub/climate/resume') return json(SCHEDULED_THERMOSTAT);
      if (method === 'POST' && p === '/api/garden/profile/detect-zone') {
        return json({ zone: '6b', zone_source: 'auto', latitude: 36, longitude: -86.8,
          last_spring_frost_mmdd: '04-15', first_fall_frost_mmdd: '10-15',
          season_length_days: 170, message: 'Zone detected' });
      }
      if (method === 'POST' && /^\/api\/homehub\/schedules\/[^/]+\/enabled$/.test(p)) return json({ schedule_id: p.split('/')[4], enabled: false });
      if (p === '/api/homehub/automations/validate') return json({ valid: true, errors: [], unsupported_reason: null, warnings: [] });
      if (method === 'POST' && /^\/api\/homehub\/automations\/(automation|script)$/.test(p)) {
        return json({ item: { ...AUTOMATION, id: 'a2', alias: 'New' }, config: (body as { config?: unknown })?.config ?? {} });
      }
      const helperCall = /^\/api\/homehub\/helpers\/([a-z_]+)\/([a-z0-9_]+)\/call$/.exec(p);
      if (helperCall) {
        const row = helpers.find((h) => h.domain === helperCall[1] && h.id === helperCall[2]);
        if (!row) return json({ detail: 'No such helper' }, 404);
        const service = (body as { service?: string })?.service;
        const st = row.state;
        if (service === 'turn_on' || service === 'turn_off') st.state = service === 'turn_on' ? 'on' : 'off';
        if (service === 'increment') st.state = String(Number(st.state) + 1);
        if (service === 'decrement') st.state = String(Number(st.state) - 1);
        if (service === 'start') { st.state = 'active'; st.attributes = { ...st.attributes, finishes_at: iso(10), remaining: '0:10:00' }; }
        if (service === 'cancel' || service === 'finish') { st.state = 'idle'; st.attributes = { duration: st.attributes.duration }; }
        return json(row);
      }
      const helperOne = /^\/api\/homehub\/helpers\/([a-z_]+)(?:\/([a-z0-9_]+))?$/.exec(p);
      if (helperOne) {
        const [, domain, id] = helperOne;
        if (method === 'DELETE') {
          helpers = helpers.filter((h) => !(h.domain === domain && h.id === id));
          return json({ deleted: `${domain}.${id}` });
        }
        const conf = (body ?? {}) as Record<string, unknown>;
        const name = String(conf.name ?? '');
        if (!name) return json({ message: 'a helper needs a name', detail: 'a helper needs a name' }, 400);
        const newId = id ?? name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
        const state = domain === 'input_select' ? String((conf.options as string[] | undefined)?.[0] ?? '')
          : domain === 'timer' ? 'idle' : domain === 'counter' ? String(conf.initial ?? 0) : 'off';
        const row = helperRow(domain, newId, name, state, domain === 'input_select' ? { options: conf.options } : {}, conf);
        helpers = [...helpers.filter((h) => !(h.domain === domain && h.id === newId)), row];
        return json(row);
      }
      // Minted with a POST; without it no camera picture is ever signed.
      if (p === '/api/media/token') return json({ token: 'test-media-token', expires_at: 4102444800, user: profile.username });
      if (p === '/api/admin/cameras/recording') {
        return json({ ...MOTION_RECORDING, configured: true, enabled: true, state: 'loaded', account: 'household' });
      }
      const lock = /^\/api\/entity-protection\/([^/]+)$/.exec(p);
      if (method === 'PUT' && lock) {
        const want = body as { protected: boolean; permitted_usernames: string[] };
        const id = decodeURIComponent(lock[1]);
        if (want.protected) locks.set(id, want.permitted_usernames); else locks.delete(id);
        return json({ entity_id: id, permitted_usernames: want.protected ? want.permitted_usernames : [] });
      }
      const entityPatch = /^\/api\/homehub\/entities\/([^/]+)$/.exec(p);
      if (method === 'PATCH' && entityPatch && typeof (body as { rotation?: unknown })?.rotation === 'number') {
        rotations.set(decodeURIComponent(entityPatch[1]), (body as { rotation: number }).rotation);
      }
      const listWrite = /^\/api\/lists\/([^/]+)\/(items|clear_completed)(?:\/([^/]+))?(?:\/(move))?$/.exec(p);
      if (listWrite && listOf(decodeURIComponent(listWrite[1]))) {
        const list = listOf(decodeURIComponent(listWrite[1]))!;
        const uid = listWrite[3] ? decodeURIComponent(listWrite[3]) : null;
        const b = (body ?? {}) as { summary?: string; status?: string; previous_uid?: string | null; description?: string | null };
        if (listWrite[2] === 'clear_completed') list.items = list.items.filter((i) => i.status !== 'completed');
        else if (method === 'POST' && !uid) {
          list.items.push({ uid: `n${list.items.length + 1}`, summary: String(b.summary), status: 'needs_action', due: null,
            description: null });
        } else if (method === 'DELETE') list.items = list.items.filter((i) => i.uid !== uid);
        else if (listWrite[4] === 'move') {
          const item = list.items.find((i) => i.uid === uid)!;
          list.items = list.items.filter((i) => i.uid !== uid);
          const at = b.previous_uid ? list.items.findIndex((i) => i.uid === b.previous_uid) + 1 : 0;
          list.items.splice(at, 0, item);
        } else if (method === 'PATCH') {
          const item = list.items.find((i) => i.uid === uid)!;
          if (b.status) item.status = b.status;
          if (b.summary) item.summary = b.summary;
          if ('description' in b) {
            // The gateway refuses notes on a list that keeps none (todo_routes._due_fields).
            if (!list.features.description) return json({ detail: 'This list has no descriptions' }, 400);
            item.description = b.description ?? null;
          }
        }
        list.open = list.items.filter((i) => i.status !== 'completed').length;
        return json(list);
      }
      if (method === 'PATCH' && entityPatch && typeof (body as { voice?: unknown })?.voice === 'boolean') {
        voices.set(decodeURIComponent(entityPatch[1]), (body as { voice: boolean }).voice);
      }
      // A notice button tapped in the bell (a Home question's Confirm).
      if (p === '/api/push/action') return json({ status: 'SUCCESS', message: 'Sent.' });
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
    // The Admin page reads the family and the shared-service defaults.
    if (p === '/api/users') return json([profile]);
    if (p === '/api/users/service-defaults') return json({});
    if (p === '/api/info') return json({ service: 'jarvis-ui', version: 'e2e', git_sha: 'e2e' });

    // The home pages.
    if (p === '/api/dashboard/rooms') return json(rooms);
    if (p === '/api/dashboard/climate') return json({ thermostats: [DASH_THERMOSTAT] });
    if (p === '/api/dashboard/history') return json({ series: {} });
    if (p === '/api/dashboard/logbook') return json(logbook(url.searchParams.get('area_id')));
    if (p === '/api/lists') return json({ lists });
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
    if (p === '/api/homehub/automations') {
      return json({ items: options.automations ?? [AUTOMATION], can_edit: role === 'admin' });
    }
    if (p === '/api/homehub/automations/catalog') {
      return json({
        triggers: [{ type: 'time', label: 'At a time', fields: [{ name: 'at', label: 'Time', kind: 'time', required: true }] },
          { type: 'conversation', label: 'Someone says a sentence',
            fields: [{ name: 'command', label: 'Sentences', kind: 'lines', required: true }] }],
        conditions: [{ type: 'template', label: 'Template is true',
          fields: [{ name: 'value_template', label: 'Template', kind: 'template', required: true }] }],
        actions: [
          { type: 'repeat', label: 'Repeat', fields: [] },
          { type: 'parallel', label: 'Run steps at the same time', fields: [] },
          { type: 'wait_for_trigger', label: 'Wait for a trigger', fields: [{ name: 'timeout', label: 'Timeout', kind: 'duration' },
            { name: 'continue_on_timeout', label: 'Continue on timeout', kind: 'boolean' }] },
          { type: 'condition', label: 'Continue only if', fields: [] },
          { type: 'service', label: 'Call a service', fields: [] },
          { type: 'set_conversation_response', label: 'Reply to the person speaking',
            fields: [{ name: 'set_conversation_response', label: 'Reply', kind: 'template', required: true }] },
          { type: 'satellite_timer', label: 'Show a timer on a voice satellite', service: 'assist_satellite.timer_event', fields: [] },
        ],
        services: [
          { domain: 'assist_satellite', services: { timer_event: { name: 'timer_event', fields: {
            event: { name: 'Event', required: true, selector: { select: { options: ['started', 'updated', 'cancelled', 'finished'] } } },
            timer_id: { name: 'Timer id', required: true, selector: { text: {} } },
            total_seconds: { name: 'Length (seconds)', selector: { number: { min: 0 } } },
          } } } },
          { domain: 'tts', services: { speak: { name: 'speak', fields: { media_player_entity_id: {}, message: {}, options: {} } } } },
        ],
        entities: [{ entity_id: 'assist_satellite.den_voice', name: 'Den voice', domain: 'assist_satellite' }],
        areas: [], zones: [], persons: [], devices: [],
      });
    }
    if (p === '/api/homehub/automations/automation/a1') {
      return json({ item: AUTOMATION, config: { alias: AUTOMATION.alias, triggers: [], conditions: [], actions: [] },
        yaml: 'alias: Porch light at dusk\ntriggers: []\nactions: []\n', can_edit: role === 'admin' });
    }
    // The voices tts.speak takes: Jarvis's Kokoro voices and a made-up cloned voice on alpaca.
    if (p === '/api/homehub/automations/voices') {
      return json({ voices: [{ id: 'af_heart', name: 'af_heart', kind: 'kokoro' }, { id: 'am_adam', name: 'am_adam', kind: 'kokoro' },
        { id: 'pat-0a1b2c', name: 'Pat', kind: 'clone' }], default: 'af_heart', errors: {} });
    }
    if (p === '/api/homehub/automations/remap') return json({ remap: {}, report: [] });
    if (p === '/api/homehub/automations/blueprints') return json({ blueprints: [BLUEPRINT] });
    if (p === `/api/homehub/automations/blueprints/${BLUEPRINT.path}`) return json({ ...BLUEPRINT, used_by: [] });
    if (p === '/api/homehub/helpers') {
      return json({ helpers, types: HELPER_TYPES, problems: ['input_number Old level: max must be greater than min'] });
    }
    if (p === '/api/cameras') {
      return json({ cameras: CAMERAS.map((c) => (rotations.has(c.entity_id) ? { ...c, rotation: rotations.get(c.entity_id) } : c)) });
    }
    if (/^\/api\/cameras\/[^/]+\/recordings$/.test(p)) {
      return json({ recordings: [
        { file: 'r1.mp4', start: iso(-120), end: iso(-119), duration: 42, triggers: ['person', 'motion'] },
        // A motion clip the hub saved already turned a quarter turn.
        { file: 'r2_r90.mp4', start: iso(-240), end: iso(-239), duration: 12, triggers: ['motion'], rotation: 90 },
      ] });
    }
    if (p === '/api/admin/cameras/live-video') {
      return json({ configured: true, entry_id: 'go2rtc-1', enabled: true, state: 'loaded', reason: null,
        url: 'http://192.0.2.30:1984', username: '', has_password: false,
        options: { webrtc_host: '', webrtc_port: 8555, default_quality: 'sub' }, server: { version: '1.9.4', error: null },
        cameras: [
          { entity_id: 'camera.yard', name: 'Yard', platform: 'reolink', settings: {}, qualities: ['sub', 'main'],
            registered: ['sub', 'main'], default_quality: 'sub' },
          { entity_id: 'camera.porch', name: 'Porch camera over the side gate with a long name', platform: 'reolink',
            settings: { on_demand: true }, qualities: ['sub'], registered: [], battery: true, on_demand: true,
            note: 'It runs on a battery, so it streams only while someone watches.' },
        ] });
    }
    if (p === '/api/admin/cameras/recording') return json(MOTION_RECORDING);
    // Admin > Network (a made-up router and devices).
    if (p === '/api/network/status') {
      return json({ configured: true, connected: true, url: 'https://192.0.2.1', username: 'jarvis', has_password: true,
        radios: [], devices: {}, networks: [], events: [], internet: { up: true }, checked_at: iso(-1) });
    }
    if (p === '/api/network/usage') return json({ hours: 24, devices: [], networks: [], series: [], bucket: 'hour', source: 'nlbw' });
    if (p === '/api/network/log') return json({ lines: [], source: 'forwarded' });
    if (p === '/api/network/outages') return json({ days: 30, outages: [] });
    if (p === '/api/network/sites/devices') {
      const now = Date.now() / 1000;
      return json({ hours: Number(url.searchParams.get('hours') || 24), receiving: true, devices: [
        { device: '02:00:00:00:00:50', name: 'Robin’s phone with a long name for small screens', ip: '192.0.2.50',
          network: 'devices', lookups: 1234, domains: 87, last: now - 120 },
        { device: '02:00:00:00:00:51', name: 'Living room TV', ip: '192.0.2.51', network: 'devices', lookups: 310, domains: 12, last: now - 7200 },
      ] });
    }
    if (p === '/api/network/sites') {
      const now = Date.now() / 1000;
      return json({ hours: 24, sites: [
        { device: '02:00:00:00:00:50', name: 'Robin', ip: '192.0.2.50', domain: 'video.example.com', lookups: 400, last: now - 60 },
        { device: '02:00:00:00:00:50', name: 'Robin', ip: '192.0.2.50', domain: 'img.example.com', lookups: 200, last: now - 600 },
        { device: '02:00:00:00:00:50', name: 'Robin', ip: '192.0.2.50', domain: 'news.example.org', lookups: 90, last: now - 900 },
        { device: '02:00:00:00:00:50', name: 'Robin', ip: '192.0.2.50', domain: 'a-very-long-subdomain-name-for-small-phones.example.net', lookups: 5, last: now - 9000 },
      ] });
    }
    if (p === '/api/dashboard/cameras/camera.yard/controls') {
      return json({ ...YARD_CONTROLS, settings: role === 'admin' ? YARD_CONTROLS.settings : [] });
    }
    if (/^\/api\/dashboard\/cameras\/[^/]+\/controls$/.test(p)) {
      return json({ entity_id: decodeURIComponent(p.split('/')[4]), actions: [], status: [], settings: [] });
    }
    const viewersOf = /^\/api\/admin\/cameras\/([^/]+)\/viewers$/.exec(p);
    if (viewersOf) {
      const id = decodeURIComponent(viewersOf[1]);
      const permit = locks.get(id);
      // Without a lock, the made-up roles decide: Robin's role covers every device, Sky has none assigned.
      const watches = (name: string) => (permit ? permit.includes(name) : name === 'robin');
      return json({ entity_id: id, limited: Boolean(permit), permitted: permit ?? [], people: [
        { username: 'parent', name: 'Parent', is_admin: true, always: true, can_watch: true },
        { username: 'robin', name: 'Robin', is_admin: false, always: false, can_watch: watches('robin') },
        { username: 'sky', name: 'Sky With A Very Long Display Name For Small Phones', is_admin: false, always: false,
          can_watch: watches('sky') },
      ] });
    }
    if (p === '/api/homehub/discovered') return json({ ...DISCOVERED, devices: [...DISCOVERED.devices, MOVED_FINDING] });
    const registry = registries.answer(method, p, null);
    if (registry) return json(registry.body, registry.status);
    if (p === '/api/homehub/hardware') return json(HARDWARE);
    // No device is in the hub twice here (e2e/home/devices-duplicates.spec.ts mocks some).
    if (p === '/api/homehub/devices/duplicates') return json({ groups: [], integrations: [], rules: [] });
    // A device's Restarts section (ESPHome only): hidden unless a spec mocks it (e2e/home/devices-restarts.spec.ts).
    const restarts = /^\/api\/homehub\/devices\/([^/]+)\/restarts$/.exec(p);
    if (restarts) return json({ supported: false, device_id: decodeURIComponent(restarts[1]) });
    // No light groups yet (e2e/home/light-groups.spec.ts mocks some); the Devices page asks.
    if (p === '/api/homehub/light_groups' && method === 'GET') return json({ groups: [], lights: [] });
    if (p.startsWith('/api/homehub/hardware/')) {
      return json(detailFor(decodeURIComponent(p.split('/').pop() || ''), initialStates(), voices));
    }
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
    // The family PC installer (Devices, Family): none on the server in these mocks.
    if (p === '/api/app-updates/windows/info') {
      return json({ available: false, version: null, sha256: null, git_sha: null, built_at: null, files: [] });
    }

    // Garden (Dashboard widget, Recipes shelf, garden planner tab).
    if (p === '/api/garden/today') {
      return json({ date: '2026-01-01', due_count: 0, overdue_count: 0, by_room: {}, all: [] });
    }
    if (p === '/api/garden/harvest/ready') return json({ count: 0, ready: [] });
    if (p === '/api/garden/recipes/matches') return json({ ready_ingredients: [], matches: [] });
    if (p === '/api/users/family-directory') {
      return json([
        { username: 'parent', display_name: 'Parent', nextcloud_user: null, avatar: null, family_role: 'mother' },
        { username: 'child', display_name: 'Child', nextcloud_user: null, avatar: null, family_role: 'child' },
      ]);
    }
    if (/^\/api\/garden\/plants\/[^/]+\/skylight-options$/.test(p)) {
      return json({
        can_assign_skylight: false, skylight_configured: false, eligible_assignees: [],
        current_chore_id: null, current_assignee: null, skylight_sync_enabled: false,
      });
    }
    if (p === '/api/garden/plants') return json([]);
    if (p === '/api/garden/locations') return json([]);
    if (p === '/api/garden/profile') {
      return json({ user_id: 'household', family_size: 7, hardiness_zone: '6b', zone_source: 'manual',
        last_spring_frost_mmdd: '04-15', first_fall_frost_mmdd: '10-15', units: 'imperial',
        preservation_prefs: null, daily_digest_time: '08:00', yard_canvas_json: null,
        created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' });
    }
    if (p === '/api/garden/streaks') {
      return json({ user_id: 'parent', current_streak: 0, longest_streak: 0, total_logs: 0 });
    }
    if (p === '/api/garden/pantry-goals') return json({ count: 0, goals: [] });
    if (p === '/api/garden/plans') return json({ count: 0, plans: [] });
    if (p === '/api/garden/species/custom') return json({ count: 0, results: [] });
    if (p === '/api/garden/species/search') {
      return json({ query: url.searchParams.get('q') || '', category: null, count: 0, results: [], seed_present: true });
    }
    if (p === '/api/garden/species') {
      return json({ category: null, usda_zone: null, count: 0, results: [], seed_present: true });
    }
    // No deploy under way (the "Jarvis is updating" flag, served from a file: 404 when there is none).
    if (p === '/api/deploy-status') return route.fulfill({ status: 404, body: '' });
    unmocked.push(`${method} ${p}`);
    return route.fulfill({ json: [] });
  });
  // Camera pictures: a 1x1 image instead of a network error; recordings: a tiny video that really plays.
  await page.route('**/api/media/cameras/**', (route) => (route.request().url().includes('/recordings/play')
    ? route.fulfill({ contentType: 'video/webm', body: Buffer.from(TINY_WEBM_BASE64, 'base64') }) :
    route.fulfill({ contentType: 'image/png', body: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64') })));
  // Image entities (a vacuum's map): a small picture through the same signed media route.
  await page.route('**/api/media/images/**', (route) => route.fulfill({ contentType: 'image/png', body: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64') }));
  return { unmocked, posts };
}
