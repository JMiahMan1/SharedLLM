import type { Page, Route } from '@playwright/test';
import { PROFILES } from './homeMocks';
import { isServerCall, INTEGRATIONS_STATUS_PATH, integrationsStatus } from './serverCalls';

/**
 * A made-up household for Settings, Admin, Identity and Remote, answered in the
 * browser. Register it after mockHome (and mockHealth, which claims /api/geo/*):
 * the last route registered answers first, and anything not listed here falls
 * back to the earlier mocks. Names are invented, hosts are example.test or
 * 192.0.2.x, and nothing here is a real secret. Long names, long text and an
 * unbroken long word are there on purpose, to stress narrow layouts.
 */

const NOW = Date.now();
const iso = (offsetMin = 0) => new Date(NOW + offsetMin * 60_000).toISOString();

export const LONG_WORD = 'Supercalifragilisticexpialidociousnessreallylongunbrokenword';
export const LONG_PERSON = 'Bartholomew-Maximilian Featherstonehaugh-Worthington';

const ME = { ...PROFILES.admin, full_name: LONG_PERSON, display_name: LONG_PERSON, avatar: null,
  ha_url: 'https://ha.example.test', saved_credentials: ['ha_token', 'nextcloud_pass'], nextcloud_url: 'https://cloud.example.test',
  nextcloud_user: 'parent', has_password: true, role_ids: [1], permissions: ['devices.use_all', 'devices.assign'] };

const USERS = [
  ME,
  { id: 2, username: 'kid', display_name: 'Kid', full_name: 'Kid', role: 'user', is_admin: false, family_role: 'child', is_minor: true,
    has_password: true, role_ids: [], saved_credentials: [] },
  { id: 3, username: 'robin', display_name: 'Robin', full_name: 'Robin Anneliese Featherstonehaugh-Worthington', role: 'user',
    is_admin: false, family_role: 'teen', is_minor: true, has_password: true, role_ids: [2], saved_credentials: [] },
  { id: 4, username: 'mom', display_name: 'Mom', full_name: 'Mom', role: 'admin', is_admin: true, family_role: 'mother',
    is_minor: false, has_password: true, role_ids: [1], saved_credentials: ['ha_token'] },
  { id: 5, username: 'grandparentwithaverylongusernamethatneverbreaks', display_name: 'Grandparent', full_name: 'Grandparent', role: 'user',
    is_admin: false, family_role: 'other', is_minor: false, has_password: false, role_ids: [], saved_credentials: [] },
];

const FAMILY = USERS.map((u) => ({ username: u.username, display_name: u.full_name, family_role: u.family_role,
  mother_username: u.is_minor ? 'mom' : null, is_minor: u.is_minor, sharing_locked: u.is_minor, is_admin: u.is_admin, avatar: null }));

const ENTITIES = [
  { entity_id: 'light.living_room_lamp', friendly_name: 'Living room lamp', state: 'on', domain: 'light' },
  { entity_id: 'light.kitchen_ceiling_light_over_the_island_and_the_long_counter', friendly_name: 'Kitchen ceiling light over the island and the long counter', state: 'off', domain: 'light' },
  { entity_id: 'media_player.den_tv', friendly_name: 'Den TV', state: 'playing', domain: 'media_player' },
  { entity_id: 'media_player.kitchen_speaker', friendly_name: 'Kitchen speaker', state: 'idle', domain: 'media_player' },
  { entity_id: 'switch.barn_pump', friendly_name: 'Barn pump', state: 'off', domain: 'switch' },
  { entity_id: 'sensor.barn_power', friendly_name: 'Barn power', state: '412', domain: 'sensor' },
];

const SETTINGS = [
  { key: 'weather_location', value: JSON.stringify({ name: 'Home farm', latitude: 36.1, longitude: -86.8 }), description: 'Forecast place' },
  { key: 'weather_units', value: 'imperial', description: 'Forecast units' },
  { key: 'household_device_access', value: 'unlocked', description: 'What a voice satellite may control when it does not recognise who is speaking' },
  { key: 'weather_reminders', value: JSON.stringify([{ id: 'r1', kind: 'freeze', threshold: 32, enabled: true, label: 'Drip the taps' }]) },
  { key: 'default_model', value: 'qwen3:8b', description: 'The model Jarvis uses when nothing else is chosen' },
  { key: 'a_setting_with_a_long_value', value: `${LONG_WORD}${LONG_WORD}`, description: `A description that goes on and on to see how a long line of text wraps on a small phone screen ${LONG_WORD}` },
  { key: 'raven_scan_interval', value: '300', description: 'Seconds between Raven scans' },
];

const DEVICES = [
  { id: 1, device_id: 'kitchen_display', user_id: 1, username: 'parent' },
  { id: 2, device_id: `satellite_${LONG_WORD.toLowerCase()}`, user_id: 3, username: 'robin' },
  { id: 3, device_id: 'den_voice', user_id: 2, username: 'kid', revoked: true },
];

const ROLES = {
  permissions: [
    { key: 'devices.use_all', description: 'Control every device that is not locked' },
    { key: 'devices.assign', description: 'Choose who can use each device' },
    { key: 'media.manage', description: `Manage media groups and speakers across the whole house, including ${LONG_WORD}` },
  ],
  roles: [
    { id: 1, name: 'Home manager', description: 'Runs the house.', permissions: ['devices.use_all', 'devices.assign'], builtin: true,
      builtin_key: 'mother', members: ['parent', 'mom'] },
    { id: 2, name: 'Teen with a role name long enough to wrap on a phone', description: `Plays music and turns on lights. ${LONG_WORD}`,
      permissions: ['media.manage'], builtin: false, members: ['robin'] },
  ],
};

const COMPANIONS = [
  { device_key: 'phone-1', kind: 'phone', label: 'Parent’s phone with a long label for small screens', owner_username: 'parent',
    registered_by: 'self', model: 'Pixel 7', manufacturer: 'Google', os_version: 'Android 15', app_version: '1.42.0',
    last_ip_address: '192.0.2.40', last_seen_at: iso(-3), first_seen_at: iso(-60 * 24 * 30),
    battery: { at: iso(-5), pct: 64, usb: false } },
  { device_key: 'watch-1', kind: 'watch', label: 'Watch', owner_username: 'parent', registered_by: 'paired', model: 'Jarvis Watch',
    hardware: 'esp32-s3', esphome_version: '2026.10.1', last_ip_address: '192.0.2.41', last_seen_at: iso(-60), battery: null },
  { device_key: `assistant-${LONG_WORD.toLowerCase()}`, kind: 'assistant', label: LONG_WORD, owner_username: 'robin',
    registered_by: 'admin', last_seen_at: null },
];

const DROP_IN = {
  me: 'parent',
  people: [
    { username: 'parent', name: LONG_PERSON, minor: false },
    { username: 'mom', name: 'Mom', minor: false },
    { username: 'kid', name: 'Kid', minor: true },
    { username: 'robin', name: 'Robin Anneliese Featherstonehaugh-Worthington', minor: true },
  ],
  settings: [
    { username: 'parent', name: LONG_PERSON, minor: false, allow_from: ['mom'], configured: true, can_edit: true,
      devices: [{ device_key: 'phone-1', label: 'Parent’s phone with a long label for small screens', kind: 'phone', drop_in: true, drop_in_video: false }] },
    { username: 'kid', name: 'Kid', minor: true, allow_from: ['parent', 'mom'], configured: true, can_edit: true,
      devices: [{ device_key: 'tablet-2', label: 'Kid’s tablet', kind: 'tablet', drop_in: true, drop_in_video: true }] },
    { username: 'robin', name: 'Robin Anneliese Featherstonehaugh-Worthington', minor: true, allow_from: [], configured: false,
      can_edit: true, devices: [] },
  ],
};

const SCHEDULES = [
  { id: 'job-health-daily', user: 'parent', type: 'health', period: 'daily', run_at: '20:00', timezone: 'America/Chicago', enabled: true,
    next_run_at: iso(60 * 5), last_run_at: iso(-60 * 19), last_status: 'ready', last_error: null, attempts: 0 },
  { id: 'job-power-weekly', user: 'parent', type: 'power', period: 'weekly', run_at: '07:30', timezone: 'America/Chicago', enabled: false,
    next_run_at: null, last_run_at: iso(-60 * 24 * 3), last_status: 'failed', last_error: `The energy service did not answer in time ${LONG_WORD}`, attempts: 3 },
];

const REPORTS = [
  { id: 'rep-1', user: 'parent', type: 'health', period: 'daily', status: 'ready',
    analysis: `You slept 7h 40m and walked 9,812 steps. A long analysis line to wrap: ${LONG_WORD}.`, stats: { steps: 9812 },
    window_start: iso(-60 * 24), window_end: iso(), generated_at: iso(-30) },
  { id: 'rep-2', user: 'parent', type: 'power', period: 'weekly', status: 'no_data', analysis: null, stats: {}, generated_at: iso(-60 * 24 * 2) },
];

const SAVED_PLACES = [
  { id: 'home', entity_id: 'zone.home', name: 'Home', lat: 36.1, lon: -86.8, radius: 120, scope: 'family', owner: 'parent', important: true,
    notify: ['parent', 'mom'], icon: 'home', created_at: NOW / 1000 - 86400 * 90 },
  { id: 'school', entity_id: 'zone.school', name: `Elementary school with a long name ${LONG_WORD}`, lat: 36.12, lon: -86.79,
    radius: 200, scope: 'family', owner: 'mom', important: false, notify: [], created_at: NOW / 1000 - 86400 * 20 },
  { id: 'gym', entity_id: 'zone.gym', name: 'Gym', lat: 36.13, lon: -86.77, radius: 80, scope: 'user', owner: 'parent', important: false,
    notify: [], created_at: NOW / 1000 - 86400 * 2 },
];

const VEHICLES = [
  { id: 'van', name: 'Family van with a very long nickname for small screens', mpg: 22, cost_per_gallon: 3.19, fuel_type: 'regular',
    fuel_price_location: 'Nashville, TN', fuel_price_source: 'manual', fuel_price_updated_at: iso(-60 * 24) },
  { id: 'truck', name: 'Truck', mpg: 17, cost_per_gallon: 3.49, fuel_type: 'diesel' },
];

const GEO_TELEMETRY = {
  status: 'SUCCESS', entity_id: 'device_tracker.parent_phone', friendly_name: 'Parent phone', latitude: 36.1, longitude: -86.8,
  accuracy: 12, battery: 64, is_moving: false, current_speed_mph: 0, top_speed_mph: 61, current_zone: 'Home', closest_zone: 'Home',
  closest_zone_distance_miles: 0, distance_to_home_miles: 0, dwell_time_seconds: 7200, dwell_time_formatted: '2h 0m',
  distance_traveled_miles: 23.4, frequented_locations: [{ name: 'Home', dwell_seconds: 60000, dwell_formatted: '16h 40m' },
    { name: `Elementary school with a long name ${LONG_WORD}`, dwell_seconds: 1800, dwell_formatted: '30m' }],
  vehicle: { id: 'van', name: 'Family van', mpg: 22, cost_per_gallon: 3.19, fuel_type: 'regular', gallons_used: 1.06, estimated_cost_usd: 3.39 },
  speech: 'You are at home.',
};

const SYSTEM_HEALTH = {
  total_services: 4, running: 3, stopped: 1, unhealthy: 0,
  control_plane: { status: 'running', git_sha: '0123abcd', start_time: NOW / 1000 - 86400, uptime: '1 day, 0:00:00' },
  services: [
    { name: 'sharedllm_gateway_1', status: 'running', image: 'registry.example.test/sharedllm/gateway:latest', image_id: 'sha256:aaaa',
      started_at: iso(-60 * 24), uptime: '1 day', health: 'healthy', health_status: 'healthy', restart_count: 0, memory_usage: 214_000_000 },
    { name: 'sharedllm_identity_1', status: 'running', image: 'registry.example.test/sharedllm/identity:latest', uptime: '3 hours',
      health: 'healthy', restart_count: 2, memory_usage: 98_000_000 },
    { name: `sharedllm_${LONG_WORD.toLowerCase()}_1`, status: 'running', image: `registry.example.test/sharedllm/${LONG_WORD.toLowerCase()}:latest`,
      uptime: '5 minutes', health: null, restart_count: 0 },
    { name: 'sharedllm_execution_1', status: 'exited', image: 'registry.example.test/sharedllm/execution:latest', exit_code: 137,
      finished_at: iso(-15), restart_count: 7 },
  ],
};

const RAVEN_QUEUE = [
  { id: 1, mission_type: 'admin_fix', priority: 2, target_container: 'sharedllm_gateway_1',
    error_summary: `TypeError in the router while reading a long path ${LONG_WORD}`, proposed_mission: 'Fix the router error and add a test.',
    coding_model: 'qwen3:8b', status: 'pending', progress: 0, created_at: iso(-90), queued_at: iso(-89) },
  { id: 2, mission_type: 'improve', priority: 5, proposed_mission: 'Tidy the logging in the identity service.', status: 'completed',
    progress: 100, created_at: iso(-60 * 30), started_at: iso(-60 * 29), completed_at: iso(-60 * 28), duration: 312, result: 'merged' },
];

const AUDIT = [
  { id: 1, at: iso(-10), actor: 'parent', actor_kind: 'user', action: 'user.update', target: 'robin',
    changes: [{ field: 'ha_token', change: 'set', secret: true }, { field: 'full_name', change: 'changed', to: 'Robin Anneliese Featherstonehaugh-Worthington' }],
    source: 'ui', client: 'web', user_agent: 'Mozilla/5.0 (example)', note: null },
  { id: 2, at: iso(-120), actor: 'internal', actor_kind: 'internal', action: 'user.create', target: LONG_WORD, changes: [], source: 'sync' },
];

const LOGS = [
  { id: 1, timestamp: iso(-1), service: 'gateway', level: 'INFO', message: 'Request handled' },
  { id: 2, timestamp: iso(-2), service: 'identity', level: 'ERROR', message: `Could not reach https://cloud.example.test/remote.php/dav/files/parent/${LONG_WORD}` },
  { id: 3, timestamp: iso(-3), service: 'execution', level: 'WARNING', message: 'Slow response from the media service' },
];

const DNS = [
  { id: 1, domain: 'jarvis.example.test', record_type: 'A', values: ['192.0.2.10'], ttl: 300, is_active: true, created_at: iso(-9000), updated_at: iso(-90) },
  { id: 2, domain: `${LONG_WORD.toLowerCase()}.example.test`, record_type: 'CNAME', values: ['jarvis.example.test'], ttl: 3600,
    is_active: false, created_at: iso(-9000), updated_at: iso(-900) },
];

const PLAYERS = [
  { entity_id: 'media_player.den_tv', friendly_name: 'Den TV', state: 'playing', icon_kind: 'tv', volume_level: 0.32,
    is_volume_muted: false, media_title: `A film with an extraordinarily long title ${LONG_WORD}`, media_artist: 'Studio', app_name: 'Films' },
  { entity_id: 'media_player.bedroom_tv', friendly_name: 'Bedroom TV with a long name for the chip', state: 'off', icon_kind: 'tv' },
  { entity_id: 'media_player.kitchen_speaker', friendly_name: 'Kitchen speaker', state: 'idle', icon_kind: 'speaker', volume_level: 0.5 },
  { entity_id: 'media_player.garage_speaker', friendly_name: 'Garage speaker', state: 'unavailable', icon_kind: 'speaker' },
];

const BIBLE_ENTRY = (name: string, kind: string, installable: boolean) => ({ path: `/Library/${name}`, name, is_dir: !kind, size: 1_200_000,
  kind, installable, note: installable ? '' : 'A folder' });

const BIBLE_ADMIN: Record<string, unknown> = {
  '/api/bible/admin/imports': {
    default_version: 'tst', primary: 'tst',
    versions: [{ code: 'tst', name: 'Test Translation', language: 'en', license_class: 'public_domain', rights_holder: '', installed: true,
      verse_count: 31102, imported_at: iso(-60 * 24 * 30), editions: 1, primary: true, provider: '', note: '' },
    { code: 'lng', name: `A licensed translation with a long name ${LONG_WORD}`, language: 'en', license_class: 'licensed',
      rights_holder: 'Example Press', installed: false, verse_count: null, imported_at: null, editions: 0, primary: false, provider: 'example', note: 'Its text has to be fetched from the provider first.' }],
    editions: [{ code: 'tst-study', version: 'tst', name: 'Test Study Bible', publisher: 'Example Press', language: 'en',
      license_class: 'licensed', rights_holder: 'Example Press', installed: true, note_count: 1200, note_kinds: [], note: '' }],
    providers: [{ code: 'example', title: 'Example provider', base_url: 'https://bible.example.test', requires: 'BIBLE_EXAMPLE_KEY',
      configured: false, reason: 'Set BIBLE_EXAMPLE_KEY to use it.', note: '', translations: [] }],
    kinds: ['json', 'pdf', 'epub'], import_dir: '/data/bible/imports', import_dir_error: '',
    runs: [{ source: 'upload', kind: 'json', code: 'tst', name: 'Test Translation', provider: '', provider_id: '', status: 'succeeded',
      message: 'Imported 31,102 verses.', verse_count: 31102, book_count: 66, note_count: null, log: ['Read file', `Parsed ${LONG_WORD}`],
      duration_ms: 4200, created_at: iso(-60 * 24 * 30) },
    { source: 'library', kind: 'pdf', code: 'lng', name: 'Licensed', provider: '', provider_id: '', status: 'failed',
      message: `Could not read the PDF ${LONG_WORD}`, verse_count: null, book_count: null, note_count: null, log: [], duration_ms: 900,
      created_at: iso(-60 * 24) }],
    library_root: '/Library', library_setting: 'BIBLE_LIBRARY_ROOT', library_error: '',
  },
  '/api/bible/admin/books': { books: [{ id: 7, title: `A Made-up Study With a Long Title ${LONG_WORD}`, author: 'Test Author', kind: 'study',
    source: 'upload', source_path: '', chapter_count: 12, cadence: 'week', created_at: iso(-600) }], count: 1, kinds: ['devotional', 'study'],
  sources: { nextcloud: '/Books', calibre: '' } },
  '/api/bible/admin/books/browse': { source: 'nextcloud', root: '/Books', setting: 'BIBLE_BOOKS_ROOT', path: '', parent: '',
    entries: [BIBLE_ENTRY('Devotionals', '', false), BIBLE_ENTRY(`${LONG_WORD}.epub`, 'epub', true)], count: 2, installable: 1 },
  '/api/bible/admin/interlinear': { books: [{ book: 'John', language: 'greek', source: 'test', verses: 879 }], verse_count: 879,
    attribution: 'Test interlinear' },
  '/api/bible/admin/library/tagged': { count: 2, auto_sync_hours: 24, books: [
    { id: 1, title: 'Test Translation', authors: ['Test Author'], file: 'tst.epub', format: 'epub', code: 'tst', edition: null,
      edition_name: null, state: 'installed', reason: '', installed_at: iso(-60 * 24) },
    { id: 2, title: `Unknown Bible ${LONG_WORD}`, authors: [], file: null, format: null, code: null, edition: null, edition_name: null,
      state: 'needs attention', reason: 'The translation cannot be told from the title.', installed_at: null }] },
  '/api/bible/admin/library/concordances': { installed: { installed: true, entries: 14000, occurrences: 300000, source: 'test' },
    books: [{ id: 3, title: 'Test Concordance', authors: ['Test Author'], file: 'c.epub', state: 'installed', reason: '' }],
    job: { state: 'idle', book_id: null, title: '', message: '' }, tags: ['Concordance', 'Strong’s'] },
};

const GET: Record<string, unknown> = {
  // Shell and profile
  '/api/users/me': ME,
  '/api/users': USERS,
  '/api/users/family': FAMILY,
  '/api/users/family-directory': FAMILY.map((f) => ({ username: f.username, display_name: f.display_name })),
  '/api/users/service-defaults': { urls: { ha_url: 'https://ha.example.test', nextcloud_url: 'https://cloud.example.test' },
    source: { ha_url: 'env', nextcloud_url: 'setting' } },
  // Settings
  '/api/app-updates/version': { version: '1.42.0', git_sha: 'e2e', build_timestamp: iso(-600), release_notes: 'Fixes.',
    bundle_available: false, apk_available: false, apk_url: null },
  '/api/users/me/notification-preferences': { arrivals: true, weather: false },
  '/api/users/me/weather': { status: 'SUCCESS', location: null, units: null, follow: false, live: false },
  '/api/geo/saved-places': { places: SAVED_PLACES },
  '/api/users/me/activity-sharing': { status: 'SUCCESS', enabled: true, audience: 'some', user_ids: ['mom'],
    share: ['location', 'steps'], sharing_locked: false },
  '/api/users/sharing-recipients': USERS.filter((u) => u.username !== 'parent').map((u) => ({ username: u.username, display_name: u.full_name })),
  '/api/communication/calls/drop-in': DROP_IN,
  '/api/telemetry/schedules': { status: 'SUCCESS', jobs: SCHEDULES },
  '/api/telemetry/reports': { status: 'SUCCESS', reports: REPORTS },
  '/api/telemetry/reports/latest': { status: 'SUCCESS', report: REPORTS[0] },
  '/api/users/me/location-pause': { paused: false, until: null, allowed: true },
  '/api/geo/vehicles': { vehicles: VEHICLES },
  '/api/geo/vehicles/assigned/parent': { user_id: 'parent', vehicle_id: 'van', vehicle: VEHICLES[0] },
  '/api/geo/telemetry/parent': GEO_TELEMETRY,
  // Identity
  '/api/spotify/status': { configured: true, missing: [], linked: true, account_name: `parent.${LONG_WORD.toLowerCase()}`,
    linked_at: iso(-60 * 24 * 10), last_sync_at: iso(-15), last_error: null },
  '/api/users/me/voice': { enrolled: true, samples: 4, min_samples: 3, max_samples: 8, consistency: 0.91, model: 'ecapa', updated_at: iso(-60 * 24) },
  '/api/user-panel/devices': COMPANIONS,
  '/api/users/me/keys': [
    { id: 1, label: 'OpenWebUI on the den computer', prefix: 'sk-e2e-aaaa', created_at: iso(-60 * 24 * 40) },
    { id: 2, label: LONG_WORD, prefix: 'sk-e2e-bbbb', created_at: iso(-60) },
  ],
  // Admin: users and devices
  '/api/users/roles': ROLES,
  '/api/entities': { entities: ENTITIES },
  '/api/entity-protection': [{ entity_id: 'switch.barn_pump', permitted_usernames: ['parent', 'robin'], granted_by: 'parent',
    granted_at: iso(-60 * 24), note: `Only for chores ${LONG_WORD}` }],
  '/api/settings': SETTINGS,
  '/api/weather/integrations': { events: ['forecast.updated', 'condition.changed', 'alert.started', 'day.completed'],
    services: ['execution', 'homehub'], metric_modes: ['power', 'counter', 'mean', 'on_time'], metric_aggs: ['sum', 'mean'],
    kinds: ['freeze', 'rain', 'heat', 'wind'], stream: 'weather.events',
    metrics: [{ name: 'barn_power', label: 'Barn power', unit: 'kWh', agg: 'sum', source: 'ha' }] },
  '/api/weather/correlations': { units: '°F', today: new Date(NOW).toISOString().slice(0, 10), metrics: [
    { name: 'barn_power', label: 'Barn power', unit: 'kWh', source: 'ha', days: 40, mean: 12.4,
      fit: { baseline: 8, per_degree_day: 0.6, r2: 0.71, days: 40 }, r_temperature: -0.62, r_precip: 0.1, wet_mean: 13.1, dry_mean: 12.0,
      projection: [{ date: new Date(NOW).toISOString().slice(0, 10), value: 13 }], insights: ['Colder days use more power in the barn.'] },
  ], insights: [`Barn power rises about 0.6 kWh for each degree-day of heating. ${LONG_WORD}`] },
  '/api/auth/discover': { users: [
    { username: 'newcousin', source: 'home_assistant', display_name: 'New Cousin With A Long Display Name', email: 'cousin@example.test' },
    { username: LONG_WORD.toLowerCase(), source: 'nextcloud', nc_username: LONG_WORD.toLowerCase() },
  ], warnings: ['Audiobookshelf did not answer, so its accounts are not listed.'], errors: [] },
  '/api/users/devices': DEVICES,
  // Admin: device groups
  '/api/groups/media': { groups: [{ name: 'Downstairs speakers', member_entity_ids: ['media_player.den_tv', 'media_player.kitchen_speaker'] },
    { name: LONG_WORD, member_entity_ids: [] }] },
  // Light clusters became the hub's light groups (Devices > Light groups); Admin > Device Groups shows the same panel.
  '/api/homehub/light_groups': {
    groups: [{ entry_id: 'g1', entity_id: 'light.kitchen_lights', name: 'Kitchen lights', area_id: 'kitchen', area: 'Kitchen',
      state: 'on', missing: 0, members: [
        { entity_id: 'light.kitchen_ceiling_light_over_the_island_and_the_long_counter', name: LONG_WORD, state: 'on', missing: false, hidden: false, area: 'Kitchen' },
        { entity_id: 'light.kitchen_pendant', name: 'Pendant', state: 'off', missing: false, hidden: false, area: 'Kitchen' },
      ] }],
    lights: [{ entity_id: 'light.kitchen_pendant', name: 'Pendant', area: 'Kitchen', hidden: false, available: true }],
    areas: [{ area_id: 'kitchen', name: 'Kitchen' }],
  },
  '/api/groups/patterns': { patterns: [{ name: 'Wake up slowly', steps: [{ brightness: 10, transition: 60 }, { brightness: 200, transition: 300 }] }] },
  // Admin: telemetry, storage, intercom, raven
  '/api/telemetry/enroll': { enrollments: [
    { entity_id: 'sensor.barn_power', power_tracking: true, availability_tracking: true, usage_tracking: false, offline_alert_threshold_minutes: 30,
      enrolled_at: iso(-60 * 24 * 7) },
    { entity_id: 'light.kitchen_ceiling_light_over_the_island_and_the_long_counter', power_tracking: false, availability_tracking: true,
      usage_tracking: true, offline_alert_threshold_minutes: 120 },
  ] },
  '/api/storage/usage/system': { total: 500e9, free: 120e9, quota: 400e9, used: 280e9, users: [
    { user: 'parent', used: 120e9, quota: 200e9, free: 80e9, count: 4123, by_kind: { photos: 90e9, documents: 30e9 }, warn: false },
    { user: 'grandparentwithaverylongusernamethatneverbreaks', used: 95e9, quota: 100e9, free: 5e9, count: 912, by_kind: { videos: 95e9 }, warn: true },
  ] },
  '/api/intercom/sessions': [
    { session_id: 's1', caller_user_id: 'parent', target_room: 'Kitchen', session_type: 'twoway', status: 'active' },
    { session_id: 's2', caller_user_id: 'robin', target_user_id: LONG_WORD, session_type: 'broadcast', status: 'ringing' },
  ],
  '/api/intercom/config': { default_tts_engine: 'kokoro', defaultVoice: 'af_heart', default_volume: 0.6, enable_espresense_routing: true },
  '/api/admin/raven/config': { raven_suspended: false, raven_scan_interval: 300, raven_error_threshold: 5, active_coding_model: 'qwen3:8b',
    system_default_tts_voice: 'af_heart', system_default_tts_engine: 'kokoro', raven_max_total_seconds: 1800 },
  '/api/admin/raven/queue': RAVEN_QUEUE,
  '/api/admin/raven/tts/voices': { status: 'success', voices: ['af_heart', 'am_adam', 'bf_emma'] },
  // Settings > Voice: what anyone may choose, and Jarvis's own.
  '/api/tts/voices': { status: 'SUCCESS', voices: ['af_heart', 'am_adam', 'bf_emma', 'bm_george', 'piper:en_US-hfc_male-medium'],
    featured: [{ id: 'bm_fable,bm_lewis,bm_george', name: 'Parable Party announcer' }], default: 'piper:en_US-hfc_male-medium' },
  // Admin: LLM & settings
  '/api/dns': DNS,
  '/api/config/models': { models: ['qwen3:8b', 'qwen2.5-coder:7b', `a-model-with-a-long-name-${LONG_WORD.toLowerCase()}:q4_k_m`] },
  '/api/household-timezone': { zone: 'America/Chicago', source: 'household setting', setting: 'America/Chicago', from_location: false,
    env_tz: '', location_set: true, error: null, order: ['household setting', 'TZ in .env', 'home location'] },
  // Admin: database, services
  '/api/admin/audit': { events: AUDIT },
  '/api/admin/logs': LOGS,
  '/api/storage/stats': { total_chunks: 48211, total_documents: 1203, last_indexed: iso(-30), providers: ['nextcloud', 'bible'],
    breakdown: { nextcloud: { chunks: 40000, documents: 1100 }, [LONG_WORD]: { chunks: 8211, documents: 103 } }, status: 'SUCCESS' },
  '/api/admin/services/health': SYSTEM_HEALTH,
};

/** Answer the Settings, Admin, Identity and Remote pages' calls; everything else falls back. */
export async function mockSettingsPages(page: Page): Promise<void> {
  await page.route(isServerCall, async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === INTEGRATIONS_STATUS_PATH) return route.fulfill({ json: integrationsStatus() });
    const json = (body: unknown) => route.fulfill({ json: body });
    // Remote reads the house's players as a POST to execution.
    if (p === '/execute/media/status') return json({ status: 'SUCCESS', message: 'ok', detail: { all_players: PLAYERS } });
    if (p === '/execute/media/transport') return json({ status: 'SUCCESS', message: 'ok' });
    if (req.method() !== 'GET') return route.fallback();
    if (p in GET) return json(GET[p]);
    if (p in BIBLE_ADMIN) return json(BIBLE_ADMIN[p]);
    // Per-user drop-down reads for any family member.
    if (p.startsWith('/api/geo/vehicles/assigned/')) return json({ user_id: p.split('/').pop(), vehicle_id: null, vehicle: null });
    if (p.startsWith('/api/geo/telemetry/')) return json({ ...GEO_TELEMETRY, entity_id: `device_tracker.${p.split('/').pop()}` });
    const device = /^\/api\/user-panel\/devices\/([^/]+)\/(battery|activity)$/.exec(p);
    if (device) {
      const key = decodeURIComponent(device[1]);
      if (device[2] === 'battery') return json([0, 1, 2, 3].map((i) => ({ at: iso(-60 * (6 - i * 2)), pct: 90 - i * 8, usb: i === 0 })));
      const one = COMPANIONS.find((d) => d.device_key === key) ?? COMPANIONS[0];
      return json({ device: one, counts: { app_open: 12, battery: 40, [`a_long_event_name_${LONG_WORD.toLowerCase()}`]: 1 },
        events: [{ event: 'app_open', at: iso(-4), extra: { app: 'jarvis' } }, { event: 'battery', at: iso(-9), extra: { pct: 64 } }],
        first_seen_at: one.first_seen_at ?? null, last_seen_at: one.last_seen_at ?? null });
    }
    if (p === '/api/geo/steps/sources') {
      const day = (n: number) => new Date(NOW - n * 86_400_000).toISOString().slice(0, 10);
      return json({ user_id: 'parent', days: 7, hourly: {}, last_synced: NOW / 1000 - 600,
        sources: { 'phone-1': Object.fromEntries([0, 1, 2, 3, 4, 5, 6].map((n) => [day(n), 6000 + n * 500])) } });
    }
    const mission = /^\/api\/raven\/missions\/(\d+)$/.exec(p);
    if (mission) {
      const one = RAVEN_QUEUE.find((m) => m.id === Number(mission[1])) ?? RAVEN_QUEUE[0];
      return json({ ...one, output_log: `Reading the router.\nPatching ${LONG_WORD}.\nTests pass.`, last_llm_reply: 'Done.' });
    }
    if (p.startsWith('/api/storage/collection/')) {
      return json({ items: [
        { id: 'doc-1', document: `Notes from the family meeting. ${LONG_WORD} and more text to wrap.`,
          metadata: { type: 'note', source: `https://cloud.example.test/remote.php/dav/files/parent/${LONG_WORD}.md`, user: 'parent' } },
        { id: 'doc-2', document: 'Short.', metadata: {} },
      ] });
    }
    const shares = /^\/api\/users\/([^/]+)\/credential-shares$/.exec(p);
    if (shares) return json({ username: shares[1], services: ['home_assistant'], granted_by: 'parent', granted_at: iso(-600), shared_owner: 'parent' });
    return route.fallback();
  });
}
