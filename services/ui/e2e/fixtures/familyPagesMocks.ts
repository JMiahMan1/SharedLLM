import type { Page, Route } from '@playwright/test';
import { isServerCall } from './serverCalls';

/**
 * A made-up family for the Family hub (chat, calls, announce, games, create),
 * a child's member page, Wander and the Calendar, answered in the browser.
 * Register AFTER mockHome / mockHealth / mockBible so these answer first;
 * anything not answered here falls through to them (and to mockHome's
 * catch-all, which records it). Names are invented, and some are long on
 * purpose (a long display name, long event titles, a long unbroken word and
 * URL) so a small phone has something to wrap.
 */

export const LONG_NAME = 'Sky With A Very Long Display Name For Small Phones';
const LONG_WORD = 'Supercalifragilisticexpialidocious-and-then-some-more-letters-without-a-break';
const LONG_URL = 'https://example.com/a/very/long/path/that/never/breaks/for/small/phones/at/all?with=a&query=string';

const NOW = Date.now();
const SEC = Math.floor(NOW / 1000);
const iso = (offsetMin = 0) => new Date(NOW + offsetMin * 60_000).toISOString();

function day(offset = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Today at hh:mm local, plus `days`. */
function at(hour: number, minute = 0, days = 0): Date {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(hour, minute, 0, 0);
  return d;
}

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

// ---- The family ----------------------------------------------------------

export const DIRECTORY = [
  { username: 'parent', display_name: 'Parent', nextcloud_user: 'parent', avatar: null },
  { username: 'robin', display_name: 'Robin', nextcloud_user: 'robin', avatar: null },
  { username: 'sky', display_name: LONG_NAME, nextcloud_user: 'sky', avatar: null },
  { username: 'jordan', display_name: 'Jordan', nextcloud_user: 'jordan', avatar: null },
  { username: 'taylor', display_name: 'Taylor', nextcloud_user: 'taylor', avatar: null },
];

export const ROSTER = [
  { username: 'parent', display_name: 'Parent', family_role: 'father', mother_username: null, is_minor: false, sharing_locked: false,
    is_admin: true, avatar: null },
  { username: 'robin', display_name: 'Robin', family_role: 'mother', mother_username: null, is_minor: false, sharing_locked: false,
    is_admin: false, avatar: null },
  { username: 'sky', display_name: LONG_NAME, family_role: 'teen', mother_username: 'robin', is_minor: true, sharing_locked: true,
    is_admin: false, avatar: null },
  { username: 'jordan', display_name: 'Jordan', family_role: 'child', mother_username: 'robin', is_minor: true, sharing_locked: true,
    is_admin: false, avatar: null },
];

// ---- Chat (Nextcloud Talk) -----------------------------------------------

export const CONVERSATIONS = [
  { token: 'room-family', display_name: 'The Family Group With A Long Name For Small Phones', type: 2, last_activity: SEC - 60,
    last_message: LONG_WORD, last_message_actor: 'Robin', last_message_timestamp: SEC - 60, unread_messages: 3,
    unread_mention: true, last_read: 1, last_common_read: 1, can_moderate: true },
  { token: 'room-sky', display_name: LONG_NAME, name: 'sky', type: 1, last_activity: SEC - 3600, last_message: 'See you after practice!',
    last_message_actor: LONG_NAME, last_message_timestamp: SEC - 3600, unread_messages: 0, can_moderate: false },
  { token: 'room-jordan', display_name: 'Jordan', name: 'jordan', type: 1, last_activity: SEC - 86400 * 2,
    last_message: 'Can we have pancakes on Saturday?', last_message_actor: 'Jordan', unread_messages: 0, can_moderate: false },
  { token: 'room-trip', display_name: 'Camping trip planning', type: 2, last_activity: SEC - 86400 * 9,
    last_message: LONG_URL, last_message_actor: 'Taylor', unread_messages: 12, can_moderate: true },
];

const msg = (id: number, actor: string, name: string, message: string, ago: number, extra: Record<string, unknown> = {}) => ({
  id, token: 'room-family', actor_type: 'users', actor_id: actor, actor_display_name: name, message, timestamp: SEC - ago,
  message_type: 'comment', ...extra,
});

export const MESSAGES = [
  msg(1, 'robin', 'Robin', 'Good morning everyone! Breakfast is on the table in ten minutes.', 7200),
  msg(2, 'sky', LONG_NAME,
    'Here is a long message so the bubble has to wrap across several lines on a small phone: we finished the science project, '
    + 'the volcano works, the baking soda is all gone and we need more vinegar before Friday, plus poster board and glue.', 6000,
    { reactions: { '👍': 2, '❤️': 1 }, reactions_self: ['👍'] }),
  msg(3, 'parent', 'Parent', `An unbroken word: ${LONG_WORD}`, 5000),
  msg(4, 'jordan', 'Jordan', `Look at this: ${LONG_URL}`, 4000, { parent: { id: 2, actor_display_name: LONG_NAME, message: 'the volcano works' } }),
  msg(5, 'robin', 'Robin', 'Who is picking up the groceries?', 300),
  msg(6, 'parent', 'Parent', 'I will, on my way home from work.', 120),
];

const PARTICIPANTS = [
  { attendee_id: 1, actor_type: 'users', actor_id: 'parent', display_name: 'Parent', is_moderator: true },
  { attendee_id: 2, actor_type: 'users', actor_id: 'robin', display_name: 'Robin', is_moderator: true },
  { attendee_id: 3, actor_type: 'users', actor_id: 'sky', display_name: LONG_NAME, is_moderator: false },
  { attendee_id: 4, actor_type: 'users', actor_id: 'jordan', display_name: 'Jordan', is_moderator: false },
];

const VERSE_OF_DAY = {
  day: day(), day_of_year: 1, version: 'tst', scope: 'all', osis: 'Ps.118.24', book: 'Ps', book_name: 'Psalms', chapter: 118, verse: 24,
  reference: 'Psalm 118:24', text: 'Made-up verse text for the day, long enough that it wraps on a phone and has to be read in full.',
  context: null,
};

// ---- Announce, games and create ----------------------------------------------

const TIMERS = [
  { id: 'tm1', type: 'timer', title: 'Pizza in the oven, take it out and turn the oven off', expires_at: iso(12), active: true,
    duration_sec: 900, scope: 'family', created_by: 'parent', emoji: '🍕', target_devices: ['kitchen-speaker'] },
  { id: 'tm2', type: 'alarm', title: 'School bus', expires_at: iso(60 * 14), active: true, recurrence: 'weekdays', scope: 'family',
    created_by: 'robin' },
];

const TIMER_DEVICES = {
  devices: [
    { id: 'kitchen-speaker', name: 'Kitchen speaker', kind: 'speaker', has_speaker: true, has_screen: false, announce_capable: true },
    { id: 'living-room-tv', name: 'Living room TV with a long device name', kind: 'tv', has_speaker: true, has_screen: true,
      announce_capable: true },
    { id: 'sky-phone', name: `${LONG_NAME}'s phone`, kind: 'phone', has_speaker: true, has_screen: true },
  ],
  sounds: [{ id: 'chime', label: 'Chime', description: 'A soft chime' }],
  emojis: [{ emoji: '🍕', label: 'Pizza' }],
};

const gameCard = (slug: string, title: string, emoji: string, state: string, extra: Record<string, unknown> = {}) => ({
  slug, title, collection: 'family', family_state: state, source: 'built-in', emoji, tagline: 'A cheerful little game for the whole family',
  art: { from: '#7c5cff', to: '#ff7ab6' }, genre: 'puzzle', ages: '6+', rating: { count: 4, average: 4.5 }, plays: 12,
  top_score: { initials: 'ROB', score: 12345 }, on_shelf: state === 'published', shelf_reason: state === 'published' ? '' : 'In testing',
  ...extra,
});

const FAMILY_GAMES = {
  success: true, arcade_available: true, play_base: 'https://games.example.test', is_admin: true, admin_token_configured: true,
  min_rating: 4, min_votes: 2,
  games: [gameCard('family-cloud-hop', 'Cloud Hop', '☁️', 'published'),
    gameCard('family-checkers', 'Checkers With A Rather Long Title For Small Phones', '🏁', 'published')],
  review: [gameCard('family-orbit-guard', 'Orbit Guard', '🪐', 'testing')],
  unpublished: [gameCard('family-garden-pipes', 'Garden Pipes', '🌻', 'unpublished', { shelf_reason: 'Unpublished' })],
};

const PLAYERS = [['Robin', 'robin'], [LONG_NAME, 'sky'], ['Jordan', 'jordan'], ['Parent', 'parent']].map(([name, username], i) => ({
  username, display_name: name, initials: name.slice(0, 3).toUpperCase(), player_url: `https://games.example.test/player/${username}`,
  you: username === 'parent', avatar: null, best_score: 9000 - i * 1500, total_games: 2, games_scored: 2, submits: 5, podiums: 1,
  crowns: [2, 1, 0, 0][i],
}));

const SCOREBOARD = { success: true, arcade_available: true, players: PLAYERS };
const HIGHSCORES = {
  success: true, arcade_available: true, players: [],
  games: FAMILY_GAMES.games.map((g) => ({ slug: g.slug, title: g.title, scores: [
    { initials: 'ROB', player_id: 'robin', score: 12840 }, { initials: 'SKY', player_id: 'sky', score: 3100 }] })),
};

const ARCADE_GAMES = {
  success: true, arcade_available: true, play_base: 'https://games.example.test', featured_source: 'none', featured: [], count: 2,
  games: [
    { slug: 'space-invaders', title: 'Space Invaders', model: 'kat-coder', emoji: '👾', rating: { count: 3, average: 4 }, plays: 40,
      top_score: { initials: 'ROB', score: 9000 }, tagline: 'Defend the planet' },
    { slug: 'a-very-long-benchmark-game-slug', title: 'A Benchmark Game With A Very Long Title That Wraps', model: 'qwen3.6-35b-a3b',
      emoji: '🎮', rating: { count: 0, average: 0 }, plays: 0, top_score: null, tagline: LONG_WORD },
  ],
};

const recipe = (title: string, extra: Record<string, unknown> = {}) => ({
  title, path: `Recipes/${title}.md`, modified: iso(-120), tags: ['dinner'], meal: 'main', proteins: ['chicken'],
  total_minutes: 45, servings: 4, calories: 520, ingredients: 9, level: 2, owner: 'parent', owner_label: 'Parent',
  can_change: true, source: LONG_URL, image: null, staples: ['rice'], rating: 4.5, ratings: 3, my_rating: 5, made: 6, made_by_me: 2,
  last_made: iso(-60 * 24 * 3), ...extra,
});

const RECIPES = [
  recipe('Grandmother’s slow-cooked chicken and vegetable stew with herb dumplings', { meal: 'soup' }),
  recipe('Pancakes', { meal: 'breakfast', proteins: ['vegetarian'], total_minutes: 20 }),
  recipe(LONG_WORD, { meal: 'dessert', proteins: [], total_minutes: null, servings: null, calories: null, rating: null, ratings: 0 }),
];

// ---- Wander ----------------------------------------------------------------

const HOME = { lat: 39.7392, lon: -104.9903 };
const SCHOOL = { lat: 39.7484, lon: -104.9998 };

export const LOCATIONS = {
  robin: { latitude: SCHOOL.lat, longitude: SCHOOL.lon, accuracy: 12, speed: 0, battery: 64, charging: false, timestamp: SEC - 120,
    activity: 'still' },
  sky: { latitude: 39.7301, longitude: -104.9802, accuracy: 30, speed: 13.4, bearing: 90, battery: 18, charging: true, timestamp: SEC - 60,
    activity: 'driving' },
  jordan: { latitude: HOME.lat, longitude: HOME.lon, accuracy: 8, battery: 91, timestamp: SEC - 300, activity: 'still' },
  taylor: { latitude: 0, longitude: 0, paused: true, paused_until: SEC + 3600 },
};

const GEO_PEOPLE = {
  type: 'FeatureCollection',
  features: [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [HOME.lon, HOME.lat] }, properties: {
      entity_id: 'person.parent', friendly_name: 'Parent', state: 'home', speed: 0, battery: 77, gps_accuracy: 10,
      last_updated: iso(-2), in_zones: ['zone.home'], room: 'Kitchen', room_last_seen: iso(-1), room_stale: false } },
  ],
};

const ZONES = {
  type: 'FeatureCollection',
  features: [
    { type: 'Feature', geometry: { type: 'Point', coordinates: [HOME.lon, HOME.lat] },
      properties: { entity_id: 'zone.home', friendly_name: 'Home', latitude: HOME.lat, longitude: HOME.lon, radius: 120, important: true } },
    { type: 'Feature', geometry: { type: 'Point', coordinates: [SCHOOL.lon, SCHOOL.lat] },
      properties: { entity_id: 'zone.school', friendly_name: 'Riverside Elementary School And Community Recreation Center',
        latitude: SCHOOL.lat, longitude: SCHOOL.lon, radius: 200, important: true, notify: ['parent'] } },
  ],
};

const SAVED_PLACES = [
  { id: 'p1', entity_id: 'zone.wander_grandmas', name: 'Grandma and Grandpa’s Farmhouse Out Past The County Line', lat: 39.8, lon: -105.1,
    radius: 150, scope: 'family', owner: 'parent', important: false, notify: [], created_at: SEC - 86400 * 30 },
  { id: 'p2', entity_id: 'zone.wander_gym', name: 'Gym', lat: 39.72, lon: -104.95, radius: 80, scope: 'user', owner: 'parent',
    important: false, notify: [], icon: 'gym', created_at: SEC - 86400 * 3 },
];

const VISIT_SUGGESTIONS = [
  { lat: 39.71, lon: -104.97, count: 6, total_s: 6 * 3600, last_since: SEC - 86400, last_ts: SEC - 80000,
    place: 'Corner Coffee Roasters and Bakery on Seventeenth Avenue' },
];

const VEHICLES = [
  { id: 'v1', name: 'Family minivan with the roof box', mpg: 22, cost_per_gallon: 3.65, fuel_type: 'gasoline',
    fuel_price_location: 'Denver, CO', fuel_price_source: 'manual', fuel_price_updated_at: iso(-600) },
  { id: 'v2', name: 'Hatchback', mpg: 34, cost_per_gallon: 3.65, fuel_type: 'gasoline' },
];

const trip = (id: string, user: string, name: string, startMin: number, minutes: number, miles: number, extra: Record<string, unknown> = {}) => {
  const start = SEC + startMin * 60;
  return {
    id, user_id: user, user_name: name, vehicle_id: 'v1', vehicle_name: VEHICLES[0].name, fuel_type: 'gasoline', mpg: 22,
    cost_per_gallon: 3.65, start_time: start, end_time: start + minutes * 60, duration_seconds: minutes * 60, distance_miles: miles,
    top_speed_mph: 64, avg_speed_mph: miles / (minutes / 60), fuel_used_gal: miles / 22, trip_cost_usd: (miles / 22) * 3.65,
    activity_type: 'driving', notes: null, status: 'completed', created_at: start, updated_at: start,
    start_location: { name: 'Home', latitude: HOME.lat, longitude: HOME.lon, zone: 'zone.home' },
    end_location: { name: 'Riverside Elementary School And Community Recreation Center', address: '1234 Some Very Long Street Name, Denver, CO 80202',
      latitude: SCHOOL.lat, longitude: SCHOOL.lon },
    ...extra,
  };
};

export const TRIPS = [
  trip('t1', 'parent', 'Parent', -90, 18, 6.4, { notes: 'Dropped the kids off, then the long way round past the farmers market.' }),
  trip('t2', 'sky', LONG_NAME, -60 * 26, 42, 28.9, { is_shared: true, shared_with: [{ user_id: 'parent', user_name: 'Parent' }] }),
  trip('t3', 'robin', 'Robin', -60 * 24 * 3, 12, 3.1, { activity_type: 'cycling', vehicle_id: undefined, vehicle_name: undefined }),
  // More than the five recent ones, so "See all trips" shows.
  ...Array.from({ length: 5 }, (_, i) => trip(`t${4 + i}`, 'parent', 'Parent', -60 * 24 * (5 + i * 3), 25, 11.2 + i)),
];

const ROUTE = Array.from({ length: 8 }, (_, i) => ({
  t: SEC - 3600 + i * 120, lat: HOME.lat + (SCHOOL.lat - HOME.lat) * (i / 7), lon: HOME.lon + (SCHOOL.lon - HOME.lon) * (i / 7), spd: 12,
}));

// ---- Calendar --------------------------------------------------------------

/** The forecast the calendar puts on each day (shape as e2e/weather/weather.spec.ts). */
function forecast() {
  const icons = ['clear', 'partly_cloudy', 'rain', 'storm', 'snow', 'cloudy', 'drizzle'] as const;
  const labels = ['Clear', 'Partly cloudy', 'Rain', 'Thunderstorm', 'Snow', 'Overcast', 'Drizzle'];
  return {
    source: 'open-meteo', location: { name: 'Home', latitude: HOME.lat, longitude: HOME.lon }, timezone: 'UTC',
    utc_offset_seconds: -new Date().getTimezoneOffset() * 60, units: { system: 'imperial', temp: '°F', wind: 'mph', precip: 'in' },
    current: { ts: SEC, temp: 61.4, feels_like: 59, humidity: 40, precip: 0, code: 2, label: 'Partly cloudy', icon: 'partly_cloudy',
      wind: 4, gust: 16, is_day: true },
    hourly: [],
    daily: Array.from({ length: 7 }, (_, i) => ({ date: day(i), ts: 0, high: 78 - i * 3, low: 57 - i * 4, precip: 0, precip_prob: 5,
      snow: 0, gust: 20, code: 0, label: labels[i], icon: icons[i], sunrise: null, sunset: null })),
    fetched_at: SEC, personal: false, heads_up: [],
  };
}

const ev = (id: string, summary: string, start: Date, minutes: number | null, calendar: string, extra: Record<string, unknown> = {}) => ({
  id, summary, start_time: start.toISOString(), ...(minutes == null ? {} : { end_time: new Date(start.getTime() + minutes * 60_000).toISOString() }),
  calendar, integration: 'nextcloud', ...extra,
});

function calendarEvents() {
  return [
    ev('c1', 'Soccer practice at the community field on the far side of the park, bring water and shin guards', at(16, 30), 90,
      'sky@example.com', { location: 'Riverside Elementary School And Community Recreation Center, Field 3' }),
    ev('c2', 'Dentist', at(9), 30, 'robin@example.com'),
    ev('c3', 'Family movie night', at(19), 150, 'family', { recurring: true }),
    ev('c4', 'Grandma visits', at(0, 0, 1), null, 'family', { all_day: true, end_time: at(0, 0, 3).toISOString() }),
    ev('c5', 'Piano lesson', at(15, 45, 2), 45, 'jordan@example.com'),
    ev('c6', 'Parent-teacher conferences for every class this term', at(17, 0, 2), 60, 'robin@example.com'),
    ev('c7', 'Early meeting', at(7, 0, 2), 30, 'parent@example.com'),
    ev('c8', 'Lunch with an old friend', at(12, 0, 2), 60, 'parent@example.com'),
    ev('c9', LONG_WORD, at(10, 0, 4), 60, 'taylor@example.com'),
    ev('c10', 'Library books due', at(0, 0, 6), null, 'family', { all_day: true }),
    ev('c11', 'Bike tune-up', at(11, 0, -2), 60, 'parent@example.com'),
    ev('c12', 'Church picnic', at(13, 0, 9), 180, 'family'),
  ];
}

const CALENDAR_INTEGRATIONS = [
  { type: 'nextcloud', enabled: true, writable: true, provides_calendar: true, available: true },
  { type: 'ical', enabled: true, writable: false, provides_calendar: true, available: true, urls: [LONG_URL] },
];

const CALENDAR_SETTINGS = {
  default: 'nextcloud', disabled: [], priority: {}, ical_urls: [LONG_URL], reminder_minutes: 15, timezone: 'UTC',
  people: [
    { id: 'robin', name: 'Robin', color: '#46A758', accounts: ['robin@example.com'] },
    { id: 'sky', name: LONG_NAME, color: '#0091FF', accounts: ['sky@example.com'] },
  ],
};

// ---- A child's member page (shapes as in e2e/family-member.spec.ts) ----------

const ago = (min: number) => new Date(NOW - min * 60_000).toISOString();

function memberStatus(username: string) {
  return {
    username, day: day(), rules: {
      daily_limit_min: 120, bedtime_start: '21:00', bedtime_end: '07:00', app_limits: { 'com.roblox.client': 30 },
      earn: { steps_every: 1000, steps_min: 5, chore_min: 10, max_min: 60 }, chores_first: true,
    },
    earned_min: 30, banked_min: 15, chores_first: true, chores_left: 1,
    can_manage: true, technical: true, comms: true, protected: true, app_approvals: {}, permission_approvals: {},
    limit_min: 120, used_min: 95, bonus_min: 0, remaining_min: 25,
    bedtime_active: false, school_active: false, paused: false, locked: false, reason: null,
    until: null, resets_at: `${day(1)}T00:00:00`, blocked_apps: [], app_minutes_left: {},
    next_lock_at: new Date(NOW + 130 * 60000).toISOString(), next_lock_reason: 'bedtime',
    locked_sections: ['chat', 'bible'],
    locked_section_choices: [
      { id: 'chat', label: 'Chat', core: true }, { id: 'calls', label: 'Calls' }, { id: 'devices', label: 'Lights and the TV remote' },
      { id: 'bible', label: 'Bible' }, { id: 'music', label: 'Music' },
    ],
  };
}

function memberUsage() {
  return {
    status: 'SUCCESS',
    days: Array.from({ length: 7 }, (_, i) => ({
      day: day(-i), millis: (95 - i * 6 + (i === 3 ? 60 : 0)) * 60000, first_use: new Date().setHours(7, 5, 0, 0),
      last_use: NOW - 3 * 60000, sessions: 20 + i,
      apps: [
        { package: 'com.google.android.youtube', millis: 50 * 60000, last_use: NOW - 3 * 60000 },
        { package: 'com.roblox.client', millis: 25 * 60000, last_use: NOW - 60 * 60000 },
        { package: 'com.example.a.very.long.package.name.that.keeps.going.and.going', millis: 20 * 60000, last_use: NOW - 90 * 60000 },
      ],
    })),
  };
}

const MEMBER_ACTIVITY = {
  status: 'SUCCESS',
  events: [
    { user: 'jordan', feature: 'media', item: 'A Song With A Rather Long Title - And A Long Artist Name Too', action: 'play', at: ago(20) },
    { user: 'jordan', feature: 'web', item: 'khanacademy.org', action: 'visit', at: ago(50), detail: { visits: 4 } },
    { user: 'jordan', feature: 'web', item: 'a-very-long-subdomain-name-for-small-phones.example.net', action: 'blocked', at: ago(70),
      detail: { blocked: 1 } },
    { user: 'jordan', feature: 'bible', item: 'Psalm 23', action: 'read', at: ago(26 * 60) },
  ],
};

const MEMBER_DEVICES = {
  username: 'jordan',
  devices: [{
    device_key: 'jordan-tablet-000000000001', kind: 'tablet', label: 'Jordan’s tablet with a long device name', model: 'Tab S9',
    manufacturer: 'Example', os_version: '14', os_build: 'AP2A', app_version: '1.9.0', app_build: '512', last_ip_address: '192.0.2.42',
    last_seen_at: ago(5), first_seen_at: '2026-09-01T10:00:00',
    capabilities: { guard: { adminActive: true, deviceOwner: false, guardEnabled: true, webFilterAllowed: true, webFilterActive: true,
      usageAccess: true, reportedAt: iso() } },
    battery: { at: iso(), pct: 64, usb: false },
  }],
  tamper: [{ event: 'guard_tamper', at: ago(2 * 24 * 60), extra: { event: 'guard_disabled' }, device_key: 'jordan-tablet-000000000001' }],
  offline_code: { parent: true, robin: false },
};

function memberAnswer(p: string, url: URL): unknown {
  const member = p.match(/^\/api\/(?:screen-time|usage|guardian\/(?:recap|guidance|concerns|journal|activity))\/([^/]+)/)?.[1];
  if (p === '/api/screen-time/requests') {
    return { requests: url.searchParams.get('status') === 'pending' ? [{ id: 7, username: 'jordan', day: day(), minutes: 30,
      note: 'finish the level, please, it is the very last one before the boss and I have been trying all week', status: 'pending',
      granted_minutes: 0, created_at: iso() }] : [] };
  }
  if (p === '/api/guard/requests') {
    return { requests: url.searchParams.get('status') === 'pending' ? [{ id: 3, username: 'jordan', kind: 'app',
      target: 'com.example.blocks', label: 'Block Builder Adventure Deluxe Edition', note: '', status: 'pending', until: null,
      decided_by: null, created_at: iso(), decided_at: null }] : [] };
  }
  if (p === '/api/guard/offline-code') return { set: true, updated_at: null, can_set: true };
  if (member && p.endsWith('/status')) return memberStatus(member);
  if (p.startsWith('/api/usage/')) return memberUsage();
  if (p.startsWith('/api/guardian/concerns/')) {
    return { status: 'SUCCESS', concerns: [{ category: 'bullying', label: 'Bullying', level: 'high', excerpt: 'nobody likes you',
      feature: 'sms', at: iso() }] };
  }
  if (p.startsWith('/api/guardian/guidance/')) {
    return { status: 'SUCCESS', week: { start: day(-6), end: day() }, prompts: [{ signal: 'bullying', title: 'Unkind words',
      ref: 'Psalm 139:14', verse: 'Made-up verse text', prayer: 'Pray for Jordan.', starter: 'Has anyone said anything to you lately that hurt?' }] };
  }
  if (p.startsWith('/api/guardian/recap/')) {
    return { status: 'SUCCESS', recap: { week: { start: day(-6), end: day() }, has_data: false, screen: null, top_apps: [], media: [],
      web: { top: [], blocked: [] }, comms: { calls: 0, call_contacts: 0, messages: 0, message_contacts: 0 }, faith: { bible_days: 0 },
      concerns: [], highlights: [] } };
  }
  if (p.startsWith('/api/guardian/journal/')) return { notes: [] };
  if (p.startsWith('/api/guardian/activity/')) return MEMBER_ACTIVITY;
  if (p.startsWith('/api/recommendation-lanes/')) {
    return { username: p.split('/').pop(), lanes: { music: true, video: true, podcast: true, book: true, game: true, activity: true },
      can_manage: true };
  }
  if (p === '/api/health/family' && url.searchParams.get('day')) {
    return { viewer: 'parent', day: url.searchParams.get('day'), people: [{ user_id: 'jordan', scopes: ['sleep'],
      sleep: { duration_minutes: 540, score: 82, label: 'Good', logged: true } }] };
  }
  if (p === '/api/geo/steps' && url.searchParams.get('user_id')) {
    return { user_id: 'jordan', daily_steps: { [day()]: 6400, [day(-1)]: 9100 }, today: 6400, goal: 10000 };
  }
  if (p === '/api/integrations/skylight/chores') {
    return { status: 'SUCCESS', chores: [{ id: '50', title: 'Feed the chickens and collect every egg from the coop', completed: false,
      assignees: ['Jordan'], recurrence: 'RRULE:FREQ=DAILY' }] };
  }
  if (p === '/api/screen-time/chore-checks') {
    return { checks: [{ id: 4, username: 'jordan', day: day(), chore_id: '51', title: 'Take out the trash', status: 'pending',
      created_at: iso() }] };
  }
  if (/^\/api\/geo\/people\/[^/]+\/day$/.test(p)) {
    const t = SEC - 3 * 3600;
    return { user: 'jordan', day: url.searchParams.get('day'), points: [{ t, lat: HOME.lat, lon: HOME.lon },
      { t: t + 600, lat: SCHOOL.lat, lon: SCHOOL.lon }], stops: [], timeline: [] };
  }
  if (/^\/api\/geo\/people\/[^/]+\/driving$/.test(p)) {
    return { user: 'jordan', days: 7, trips: 0, miles: 0, drive_minutes: 0, top_speed_mph: 0,
      events: { hard_brake: 0, rapid_accel: 0, high_speed: 0, phone_use: 0, phone_use_min: 0 }, per_trip: [] };
  }
  if (p.startsWith('/api/guardian/comms/')) {
    return { days: Number(url.searchParams.get('days') || 7), events: [
      { user: 'jordan', feature: 'calls', item: 'Grandma', action: 'call', at: ago(120), duration_s: 420,
        detail: { direction: 'in', number: '+15555550101' } },
      { user: 'jordan', feature: 'sms', item: LONG_NAME, action: 'message', at: ago(60),
        detail: { direction: 'out', number: '+15555550102', body: `see you at practice ${LONG_URL}` } },
    ] };
  }
  if (/^\/api\/family\/member\/[^/]+\/devices$/.test(p)) return MEMBER_DEVICES;
  return undefined;
}

// ---- The handler -------------------------------------------------------------

/** Paths answered here (exact). */
function answer(p: string, url: URL, method: string): unknown {
  if (method !== 'GET') {
    if (p === '/api/communication/talk/conversations/open') return { status: 'SUCCESS', detail: { token: 'room-sky' } };
    // The cookbook (the Create tab) reads by POST.
    if (p === '/api/recipes/catalog') return { recipes: RECIPES, me: 'parent', is_admin: true };
    if (p === '/api/recipes/cookbook/suggestions') return { status: 'SUCCESS', detail: { suggestions: [] } };
    if (p === '/api/recipes/cookbook/personal') return { status: 'SUCCESS', detail: { titles: [] } };
    return { status: 'SUCCESS', message: 'ok' };
  }
  const forMember = memberAnswer(p, url);
  if (forMember !== undefined) return forMember;
  switch (p) {
    // Chat.
    case '/api/communication/talk/conversations': return { status: 'SUCCESS', detail: { conversations: CONVERSATIONS } };
    case '/api/communication/talk/messages': {
      const token = url.searchParams.get('token') ?? '';
      return { status: 'SUCCESS', detail: { messages: MESSAGES.map((m) => ({ ...m, token })) } };
    }
    case '/api/communication/talk/polls': return { status: 'SUCCESS', detail: { polls: [] } };
    case '/api/communication/talk/participants': return { status: 'SUCCESS', detail: { participants: PARTICIPANTS } };
    case '/api/communication/talk/people':
      return { status: 'SUCCESS', detail: { people: DIRECTORY.map((d) => ({ id: d.username, label: d.display_name })) } };
    case '/api/bible/verse-of-day': return VERSE_OF_DAY;
    case '/api/users/family-directory': return DIRECTORY;
    case '/api/users/family': return ROSTER;
    // Announce, games, create.
    case '/api/communication/timers': return TIMERS;
    case '/api/communication/timers/devices': return TIMER_DEVICES;
    case '/api/users/devices': return [];
    case '/api/arcade/family': return FAMILY_GAMES;
    case '/api/arcade/scoreboard': return SCOREBOARD;
    case '/api/arcade/highscores': return HIGHSCORES;
    case '/api/arcade/games': return ARCADE_GAMES;
    case '/api/arcade/party/rooms': return { success: true, rooms: [] };
    case '/api/integrations/skylight/meals': return { status: 'SUCCESS', configured: false, categories: [] };
    // Wander.
    case '/api/users/location/all': return LOCATIONS;
    case '/api/geo/people': return GEO_PEOPLE;
    case '/api/geo/zones': return ZONES;
    case '/api/geo/saved-places': return { places: SAVED_PLACES };
    case '/api/geo/visit-suggestions': return { suggestions: VISIT_SUGGESTIONS };
    case '/api/geo/vehicles': return { vehicles: VEHICLES };
    case '/api/geo/trips': {
      const user = url.searchParams.get('user_id');
      const trips = user ? TRIPS.filter((t) => t.user_id === user) : TRIPS;
      return { trips, total_trips: trips.length };
    }
    case '/api/geo/place-reminders': return { reminders: [] };
    case '/api/geo/eta': {
      const user = url.searchParams.get('user_id') ?? '';
      return { user_id: user, to: url.searchParams.get('to') ?? 'home', arrived: user === 'jordan', duration_s: 1260, distance_m: 14800,
        eta: SEC + 1260, moving: user === 'sky', fix_age_s: 60 };
    }
    // Calendar.
    case '/api/communication/calendar/events':
      return { status: 'SUCCESS', detail: { integrations: CALENDAR_INTEGRATIONS, default: 'nextcloud' }, events: calendarEvents() };
    case '/api/calendar/settings': return { status: 'SUCCESS', settings: CALENDAR_SETTINGS };
    case '/api/weather': return forecast();
    default: break;
  }
  const tripRoute = /^\/api\/geo\/trips\/([^/]+)\/route$/.exec(p);
  if (tripRoute) return { trip_id: tripRoute[1], points: ROUTE };
  const tripLocs = /^\/api\/geo\/trips\/([^/]+)\/locations$/.exec(p);
  if (tripLocs) {
    return { trip_id: tripLocs[1], start: { name: 'Home', lat: HOME.lat, lon: HOME.lon, source: 'ha_zone' },
      end: { name: 'Riverside Elementary School And Community Recreation Center', lat: SCHOOL.lat, lon: SCHOOL.lon, source: 'ha_zone' } };
  }
  return undefined;
}

export async function mockFamilyPages(page: Page): Promise<void> {
  // Map tiles: a 1x1 picture, so nothing reaches the network.
  await page.route(/tile\.openstreetmap\.org|basemaps\.cartocdn\.com|arcgisonline\.com|tile\.thunderforest|stadiamaps/, (route) =>
    route.fulfill({ contentType: 'image/png', body: PNG }));
  // The (made-up) arcade's pages and pictures.
  await page.route('https://games.example.test/**', (route) => (/\.(png|jpe?g|webp|svg)$/.test(new URL(route.request().url()).pathname)
    ? route.fulfill({ contentType: 'image/png', body: PNG })
    : route.fulfill({ contentType: 'text/html', body: '<body style="margin:0;background:#3b2a8a"></body>' })));
  await page.route(isServerCall, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    // The Talk long poll: hold a moment, then "nothing new", like a quiet room.
    if (p === '/api/communication/talk/messages/wait') {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [] } } }).catch(() => {});
    }
    const body = answer(p, url, req.method());
    if (body === undefined) return route.fallback();
    return route.fulfill({ json: body });
  });
}
