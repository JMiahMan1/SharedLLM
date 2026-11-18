import type { Page, Route } from '@playwright/test';
import { isServerCall, INTEGRATIONS_STATUS_PATH, integrationsStatus } from './serverCalls';

/**
 * A made-up household for the Dashboard ("/") with every widget turned on and
 * filled in: chores, timers, events, devices, notes, weather, presence, energy,
 * recommendations, a note and a picture. Names are invented, and some are long
 * on purpose (a long display name, long titles, an unbroken long word) so the
 * layout is stressed on the smallest phone.
 *
 * Register after mockHome, mockHealth and mockBible: this handler answers first
 * and falls back to them for anything it does not know.
 */

export const LONG_PERSON = 'Sky With A Very Long Display Name For Small Phones';
export const LONG_WORD = 'Supercalifragilisticexpialidocious-unbrokenwordthatkeepsgoing';

const NOW = Date.now();
const iso = (offsetMin = 0) => new Date(NOW + offsetMin * 60_000).toISOString();
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** A 1x1 PNG, so the picture widget draws a real image without the network. */
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=';

/** Every widget, each one visible, at a mix of sizes. */
const WIDGETS: Array<[string, string, Record<string, unknown>?]> = [
  ['weather', 'medium'],
  ['chores_progress', 'tall'],
  ['ambient_timer', 'medium'],
  ['upcoming_events', 'wide'],
  ['device_control', 'tall'],
  ['quick_notes', 'medium'],
  ['active_media', 'wide'],
  ['energy_insights', 'medium'],
  ['climate', 'medium', { devices: ['climate.hallway', 'climate.upstairs_bonus_room'] }],
  ['presence', 'medium'],
  ['health_activity', 'medium'],
  ['health_today', 'medium'],
  ['bible_daily', 'small'],
  ['recommendations', 'medium'],
  ['workspaces', 'small'],
  ['quick_assistant', 'medium'],
  ['note', 'medium', { title: 'Before the trip to the lake house this weekend', markdown:
    `**Pack** the cooler and the life jackets.\n\n- Sunscreen\n- Snacks for ${LONG_PERSON}\n- ${LONG_WORD}\n\n[Directions](https://example.com/lake)` }],
  ['picture', 'small', { src: PNG, caption: 'The garden in spring, before the tomatoes went in along the fence', alt: 'A garden' }],
];

const widgetSettings = () => ({
  widgets: WIDGETS.map(([key, size, config], i) => ({
    widget_key: key, visibility: 'visible', order_index: i, size, is_pinned: i === 0, sort_mode: key === 'device_control' ? 'most_used' : null,
    pinned_devices: key === 'device_control' ? ['light.living_room_reading_lamp', 'cover.garage_door'] : [], config: config ?? {}, updated_at: 0,
  })),
  quick_assistant_enabled: true,
});

const CHORES = [
  { id: 'c1', title: 'Feed the chickens and collect every egg from the nesting boxes behind the barn', completed: false,
    assignees: ['Robin'], reward: 2, recurrence: 'RRULE:FREQ=DAILY', start_time: '07:00', emoji_icon: '🐔' },
  { id: 'c2', title: 'Trash to the curb', completed: true, assignees: ['Robin'] },
  { id: 'c3', title: 'Unload the dishwasher', completed: false, assignees: [LONG_PERSON], recurrence: 'RRULE:FREQ=DAILY', start_time: '18:30' },
  { id: 'c4', title: LONG_WORD, completed: false, assignees: ['Jordan'], reward: 5 },
  { id: 'c5', title: 'Water the garden', completed: true, assignees: ['Jordan', 'Robin'] },
  { id: 'c6', title: 'Sweep the porch', completed: false, assignees: ['Parent'] },
];

const forecast = () => {
  const now = NOW / 1000;
  const hour = now - (now % 3600);
  const icons = ['clear', 'partly_cloudy', 'rain', 'storm', 'snow', 'cloudy', 'drizzle'] as const;
  const labels = ['Clear', 'Partly cloudy', 'Rain', 'Thunderstorm', 'Snow', 'Overcast', 'Drizzle'];
  return {
    source: 'open-meteo',
    location: { name: 'Home', latitude: 36, longitude: -86.8 },
    timezone: 'America/Chicago',
    utc_offset_seconds: -new Date().getTimezoneOffset() * 60,
    units: { system: 'imperial', temp: '°F', wind: 'mph', precip: 'in' },
    current: { ts: now, temp: 61.4, feels_like: 59, humidity: 83, precip: 0, code: 2, label: 'Partly cloudy',
      icon: 'partly_cloudy', wind: 4, gust: 16, is_day: true },
    hourly: Array.from({ length: 48 }, (_, i) => ({
      time: '', ts: hour + i * 3600, temp: 55 + (i % 12), precip_prob: i % 7 === 3 ? 85 : 10, precip: 0, snow: 0,
      code: 1, label: i % 7 === 3 ? 'Rain' : 'Mostly clear', icon: i % 7 === 3 ? 'rain' : 'clear', wind: 5, gust: 12,
    })),
    daily: Array.from({ length: 7 }, (_, i) => {
      const d = new Date(NOW);
      d.setDate(d.getDate() + i);
      return { date: ymd(d), ts: 0, high: 78 - i * 3, low: 57 - i * 4, precip: i === 2 ? 0.7 : 0,
        precip_prob: i === 2 ? 82 : 5, snow: 0, gust: 20, code: 0, label: labels[i], icon: icons[i], sunrise: null, sunset: null };
    }),
    fetched_at: now,
    personal: false,
    heads_up: [
      { id: 'hay', kind: 'rain', title: 'Cover the hay before the afternoon storms roll through', body:
        'Barn: Rain likely from 3 PM (85% chance, about 0.40 in). Tarps are in the barn.', starts_at: now },
      { id: 'pipes', kind: 'freeze', title: 'Drip the taps', body: 'Down to 29°F around 5 AM tomorrow.', starts_at: now + 3600 },
    ],
  };
};

const WEATHER_ENERGY = {
  units: '°F', days: [], model: { baseline_kwh: 10, kwh_per_degree_day: 0.8, r2: 0.9, days: 12 },
  insights: ['Yesterday: 34.0 kWh tracked, heat ran 6.0 h, outside averaged 35°F.'],
  projection: Array.from({ length: 7 }, (_, i) => ({ date: ymd(new Date(NOW + i * 86_400_000)), kwh: 20 + i })),
};

const EVENTS = [
  { id: 'e1', summary: 'Dentist', start_time: iso(40), end_time: iso(70), calendar: 'family', location: 'Main Street' },
  { id: 'e2', summary: 'Parent teacher conference about the spring science fair project and the field trip permission slips',
    start_time: iso(180), end_time: iso(240), calendar: 'robin@example.com', location: 'Room 214, the long hallway past the gym' },
  { id: 'e3', summary: LONG_WORD, start_time: iso(60 * 26), end_time: iso(60 * 27), calendar: 'family' },
  { id: 'e4', summary: 'Soccer practice', start_time: iso(60 * 50), end_time: iso(60 * 51), calendar: 'family', all_day: false },
  { id: 'e5', summary: 'Grandma visits', start_time: iso(60 * 75), calendar: 'family', all_day: true },
];

const CALENDAR_SETTINGS = { status: 'SUCCESS', settings: { people: [
  { id: 'robin', name: 'Robin', color: '#22c55e', accounts: ['robin@example.com'] },
  { id: 'sky', name: LONG_PERSON, color: '#f97316', accounts: ['sky@example.com'] },
] } };

const ent = (entity_id: string, friendly_name: string, state: string, area_id: string, attributes: Record<string, unknown> = {}) => ({
  entity_id, friendly_name, state, domain: entity_id.split('.')[0], area_id, attributes, last_updated: iso(-15),
});

const DEVICES = [
  ent('light.living_room_reading_lamp', 'Living room reading lamp beside the big comfortable armchair', 'on', 'living_room',
    { brightness: 180, supported_color_modes: ['brightness'] }),
  ent('light.kitchen_pendants', 'Pendants', 'off', 'kitchen', { brightness: 0 }),
  ent('switch.coffee_maker', 'Coffee maker', 'off', 'kitchen'),
  ent('cover.garage_door', 'Garage door', 'closed', 'garage', { current_position: 0 }),
  ent('lock.front_door', 'Front door', 'locked', 'entry'),
  ent('fan.bedroom_ceiling', 'Bedroom ceiling fan', 'on', 'bedroom', { percentage: 50 }),
  ent('media_player.den_tv', 'Den TV', 'playing', 'den', { volume_level: 0.3, media_title: 'A nature documentary' }),
  ent('climate.hallway', 'Downstairs', 'cool', 'hallway', { hvac_mode: 'cool', hvac_action: 'cooling', current_temperature: 76,
    temperature: 74, hvac_modes: ['off', 'heat', 'cool', 'heat_cool'], min_temp: 60, max_temp: 90, target_temp_step: 1 }),
  ent('climate.upstairs_bonus_room', 'Upstairs bonus room over the garage', 'heat', 'bonus_room', { hvac_mode: 'heat',
    hvac_action: 'idle', current_temperature: 68, temperature: 70, hvac_modes: ['off', 'heat'], min_temp: 50, max_temp: 90 }),
  ent('light.yard_flood', LONG_WORD, 'unavailable', 'unknown'),
];

const ENROLLMENTS = [
  { entity_id: 'sensor.dryer_power', power_tracking: true, availability_tracking: true, usage_tracking: true, offline_alert_threshold_minutes: 30 },
  { entity_id: 'sensor.fridge_power', power_tracking: true, availability_tracking: true, usage_tracking: true, offline_alert_threshold_minutes: 30 },
];

const telemetrySummary = (entityId: string, base: number) => ({
  entity_id: entityId,
  summary: {
    current_power_w: base + 40, peak_power_w: base * 4, peak_at: NOW / 1000 - 3600, peak_duration_seconds: 900,
    avg_power_w: base, availability_pct: 99.5, total_activations: 12, last_outage_at: null,
    data_points: Array.from({ length: 60 }, (_, i) => ({ recorded_at: NOW / 1000 - (60 - i) * 60, power_w: base + ((i * 37) % 200), is_available: true })),
  },
});

const LOCATIONS = {
  robin: { latitude: 36.16, longitude: -86.78, accuracy: 12, speed: 0, battery: 82, charging: false, timestamp: NOW / 1000 - 60, activity: 'still' },
  [LONG_PERSON]: { latitude: 36.2, longitude: -86.7, accuracy: 20, speed: 18, battery: 15, charging: true, timestamp: NOW / 1000 - 30, activity: 'driving' },
  jordan: { latitude: 36.1, longitude: -86.9, accuracy: 40, battery: 55, timestamp: NOW / 1000 - 60 * 50 },
};

const TRIPS = { total_trips: 1, trips: [{
  id: 't1', user_id: 'robin', user_name: 'Robin', mpg: 28, cost_per_gallon: 3.2, start_time: NOW / 1000 - 5400, end_time: NOW / 1000 - 3600,
  duration_seconds: 1800, distance_miles: 14.2, top_speed_mph: 61, avg_speed_mph: 28,
  start_location: { name: 'Home' }, end_location: { name: 'The hardware store out past the county line on the old highway' },
  fuel_used_gal: 0.5, trip_cost_usd: 1.6, activity_type: 'driving', status: 'completed',
}] };

const MEDIA_STATUS = { status: 'SUCCESS', message: 'ok', service: 'media', detail: {
  active: { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen speaker', state: 'playing',
    media_title: 'A very long song title that keeps going well past the edge of a small phone screen',
    media_artist: LONG_PERSON, media_album: 'Front porch', volume_level: 0.4, position: 65, duration: 240,
    media_position_updated_at: iso(0) },
  all_players: [
    { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen speaker', state: 'playing',
      media_title: 'A very long song title that keeps going well past the edge of a small phone screen',
      media_artist: LONG_PERSON, media_album: 'Front porch', volume_level: 0.4, position: 65, duration: 240,
      media_position_updated_at: iso(0) },
    { entity_id: 'media_player.den_tv', friendly_name: 'Den TV', state: 'idle' },
  ],
} };

const TIMERS = [
  { id: 't1', type: 'timer', title: 'Pasta', expires_at: iso(7), duration_sec: 600, active: true, emoji: '🍝', scope: 'user', sound: 'egg_timer' },
  { id: 't2', type: 'alarm', title: 'Pick up Jordan from the long practice at the far field', expires_at: iso(95), active: true,
    scope: 'household', target_devices: ['media_player.kitchen'] },
  { id: 't3', type: 'reminder', title: LONG_WORD, expires_at: iso(240), active: false, paused_at: iso(-2), duration_sec: 14_400, scope: 'family' },
];

const TIMER_DEVICES = { devices: [
  { id: 'phone', name: 'Mobile App (Phone Alarm)', kind: 'mobile', has_speaker: true, has_screen: true },
  { id: 'esphome:kitchen_display', name: 'Kitchen Display (ESPHome)', kind: 'esphome', has_speaker: false, has_screen: true, esphome_script: 'flash_screen' },
  { id: 'media_player.kitchen', name: 'Kitchen speaker on the counter by the window', kind: 'ha_media_player', has_speaker: true, has_screen: false },
] };

const NOTES = { status: 'SUCCESS', detail: { notes: [
  { title: 'Groceries', path: '/Notes/Groceries.md', modified: iso(-30) },
  { title: 'Ideas for the summer vacation road trip across the country and back again', path: '/Notes/Summer.md', modified: iso(-600) },
  { title: LONG_WORD, path: '/Notes/Long.md', modified: iso(-2000) },
] } };

const MEAL_PLAN = ['#meal-plan', '', ...['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
  .map((d, i) => `- [${i < 2 ? 'x' : ' '}] ${d} · ${i % 2 ? 'Tacos' : 'Slow cooker chicken and dumplings with the garden green beans on the side'}`)].join('\n');

const LOGS = [
  { timestamp: iso(-3), service: 'gateway', level: 'ERROR', message: 'INFO: 192.0.2.4:5000 - "GET /api/some/very/long/path/that/keeps/going/and/going HTTP/1.1" 500 Internal Server Error' },
  { timestamp: iso(-20), service: 'execution_service_with_a_long_name', level: 'WARNING', message: `Slow reply from ${LONG_WORD}` },
];

const WORKSPACES = { status: 'SUCCESS', workspaces: [
  { id: 'ws1', display_name: 'Household scripts', local_path: 'household', resolved_path: '/workspace/household', available: true,
    scope: 'system', capabilities: [], sync_mode: 'local_git_authoritative', auto_pull_enabled: false },
  { id: 'ws2', display_name: 'A workspace with a long name for the garden planner', local_path: 'garden',
    resolved_path: '/workspace/a/very/long/path/to/the/garden/planner/workspace/that/does/not/fit', available: false,
    scope: 'system', capabilities: [], sync_mode: 'local_git_authoritative', auto_pull_enabled: false },
] };

const MISSIONS = [
  { id: 41, mission_type: 'user_task', priority: 1, proposed_mission: 'Tidy the garden planner workspace and fix the failing watering schedule test',
    status: 'running', progress: 45, created_at: iso(-30) },
  { id: 42, mission_type: 'user_task', priority: 2, proposed_mission: LONG_WORD, status: 'queued', progress: 0, created_at: iso(-5),
    error_summary: 'Waiting for the workspace to sync before it can start' },
];

const HEALTH_READY = { status: 'DEGRADED', services: { gateway: 'OK', identity: 'OK', execution: 'OK', workspace_runtime: 'DOWN', homehub: 'OK' },
  service_details: { gateway: { git_sha: 'abc1234', start_time: NOW / 1000 - 86_400 } } };

const RECOMMENDATIONS: Record<string, unknown> = {
  music: { lane: 'folk', artist: 'The Made-up Front Porch String Band of the Hills', style: 'Bluegrass', reason: 'Bright banjo and close harmony.',
    matched_on: 'Saturday morning playlists', kind: 'music' },
  video: { theme: 'nature', creator: 'Backyard Birding', style: 'Short films', reason: 'Calm and curious.', matched_on: 'nature shows',
    kind: 'video', redeemed: true },
  podcast: { theme: 'history', show: 'Tales From The Old Mill', host: LONG_PERSON, style: 'Storytelling', reason: 'Short episodes.',
    matched_on: 'history podcasts', kind: 'podcast', redeemed: true },
  book: { theme: 'adventure', title: LONG_WORD, author: 'A. Writer', style: 'Novel', reason: 'A page turner.', matched_on: 'adventure books',
    kind: 'book', redeemed: true },
  game: { theme: 'puzzle', game: null, style: 'Puzzle', reason: 'Something new.', matched_on: 'puzzle games', kind: 'game', redeemed: false,
    redirect_to: 'A board game night with the whole family' },
  activity: { theme: 'outdoors', activity: 'A walk to the creek', style: 'Outdoors', reason: 'The weather is nice.', matched_on: 'sunny afternoons',
    kind: 'activity' },
};

const isDashboardCall = (url: URL): boolean => isServerCall(url) || url.pathname === '/health/ready' || url.pathname === '/info';

/** Answer the dashboard's calls; anything else goes on to the earlier mocks. */
export async function mockDashboard(page: Page): Promise<void> {
  await page.route(isDashboardCall, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    const method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (p === INTEGRATIONS_STATUS_PATH) return route.fulfill({ json: integrationsStatus() });

    if (method === 'POST') {
      if (p === '/execute/entity/search') {
        const domains = String((req.postDataJSON() as { domain?: string | null } | null)?.domain ?? '').split(',').filter(Boolean);
        return json({ status: 'ok', result: domains.length ? DEVICES.filter((d) => domains.includes(d.domain)) : DEVICES });
      }
      if (p === '/execute/media/status') return json(MEDIA_STATUS);
      if (p === '/api/communication/notes/list') return json(NOTES);
      if (p === '/api/communication/notes/read') return json({ status: 'SUCCESS', message: MEAL_PLAN });
      return route.fallback();
    }
    if (method !== 'GET') return route.fallback();

    switch (p) {
      case '/health/ready': return json(HEALTH_READY);
      case '/info': return json({ service: 'gateway', version: 'e2e', git_sha: 'abc1234', git_branch: 'main', build_date: '' });
      case '/api/widgets/settings': return json(widgetSettings());
      case '/api/presence/1': return json({ status: 'SUCCESS', user_id: '1', presence: { room: 'living_room', confidence: 0.9, source: 'ble' } });
      case '/api/admin/logs': return json(LOGS);
      case '/api/workspaces': return json(WORKSPACES);
      case '/api/raven/missions': return json(MISSIONS);
      case '/api/entities': return json({ entities: DEVICES.map(({ entity_id, friendly_name, state, domain }) => ({ entity_id, friendly_name, state, domain })) });
      case '/api/communication/timers': return json(TIMERS);
      case '/api/communication/timers/devices': return json(TIMER_DEVICES);
      case '/api/telemetry/enroll': return json({ enrollments: ENROLLMENTS });
      case '/api/telemetry/summary/sensor.dryer_power': return json(telemetrySummary('sensor.dryer_power', 400));
      case '/api/telemetry/summary/sensor.fridge_power': return json(telemetrySummary('sensor.fridge_power', 120));
      case '/api/calendar/settings': return json(CALENDAR_SETTINGS);
      case '/api/communication/calendar/events': return json({ status: 'SUCCESS', detail: { integrations: [] }, events: EVENTS });
      case '/api/search': return json({ answer: `The garden notes say to plant tomatoes after the last frost. ${LONG_WORD}`, files: [
        { name: 'Garden planner.md', path: '/Notes/Garden planner.md' },
        { name: `${LONG_WORD}.md`, path: `/Notes/a/very/long/folder/path/that/keeps/going/${LONG_WORD}.md` },
      ] });
      case '/api/weather': return json(forecast());
      case '/api/weather/energy': return json(WEATHER_ENERGY);
      case '/api/users/location/all': return json(LOCATIONS);
      case '/api/geo/trips': return json(TRIPS);
      case '/api/geo/eta': return json({ user_id: url.searchParams.get('user_id'), to: 'Home', arrived: false, duration_s: 1080, distance_m: 15_000,
        eta: NOW / 1000 + 1080, moving: true, fix_age_s: 30 });
      case '/api/integrations/skylight/chores': return json({ status: 'SUCCESS', chores: CHORES,
        assignee_meta: { Robin: '#22c55e', Jordan: '#3b82f6', [LONG_PERSON]: '#f97316' } });
      case '/api/integrations/skylight/members': return json({ status: 'SUCCESS', members: [
        { id: '11', label: 'Robin', color: '#22c55e' }, { id: '12', label: 'Jordan', color: '#3b82f6' },
        { id: '13', label: LONG_PERSON, color: '#f97316' },
      ] });
      case '/api/users/devices': return json([
        { id: 1, device_id: 'light.living_room_reading_lamp', user_id: 1, username: 'parent' },
        { id: 2, device_id: 'cover.garage_door', user_id: 3, username: 'robin' },
      ]);
      case '/api/users/sharing-recipients': return json([
        { username: 'parent', display_name: 'Parent' }, { username: 'robin', display_name: 'Robin' },
        { username: 'sky', display_name: LONG_PERSON }, { username: 'jordan', display_name: 'Jordan' },
      ]);
      case '/api/media/events': return route.fulfill({ contentType: 'text/event-stream', body: [
        'retry: 60000', '', 'event: heartbeat', 'data: {"type":"heartbeat"}', '',
      ].join('\n') });
      default: break;
    }
    const rec = /^\/api\/recommendations\/(\w+)$/.exec(p);
    if (rec && rec[1] in RECOMMENDATIONS) return json({ status: 'SUCCESS', recommendation: RECOMMENDATIONS[rec[1]] });
    return route.fallback();
  });
}
