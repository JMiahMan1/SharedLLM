import type { Page, Route } from '@playwright/test';
import { isServerCall } from './serverCalls';

/**
 * A made-up family's health for the hermetic screen tests: a parent with a full
 * day logged (sleep, food, water, steps, a check-in), a family challenge with a
 * long-named member, cheers, streaks and a weekly digest. Register after
 * mockHome so these answer first; anything not answered here falls through to
 * mockHome's catch-all, which records it in `unmocked`.
 */

const pad = (n: number) => String(n).padStart(2, '0');
export function day(offset = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
const at = (offsetDays: number, hh: number, mm = 0) => `${day(offsetDays)}T${pad(hh)}:${pad(mm)}:00`;

/** A name long enough to wrap or clip on a small phone. */
export const LONG_NAME = 'Emma-Grace Wellington-Montgomery';

const METRICS = {
  readiness: true, checkin: true, sleep: true, nutrition: true, water: true, steps: true, active_minutes: true,
  streaks: true, weekly_digest: true, family_challenge: true, cheers: true, lessons: true, coach: true,
  jarvis_use: true, fasting: true,
};

export const HEALTH_PROFILE = {
  user_id: 'parent', exists: true, display_name: 'Parent', birth_year: 1986, height_cm: 178, sex: 'male',
  activity_level: 'moderate', goal: 'maintain', units: 'imperial', kid_mode: false, kid_mode_locked: false,
  sleep_need_minutes: 480, water_goal_ml: 2400, weekly_active_minutes: 150, metrics: METRICS,
  metric_catalog: Object.keys(METRICS).map((key) => ({ key, label: key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()), default: key !== 'fasting' })),
  updated_at: day(),
};

const SLEEP = Array.from({ length: 14 }, (_, i) => ({
  id: 100 + i, user_id: 'parent', night: day(-i), bedtime: at(-i - 1, 22, 40), wake_time: at(-i, 6, 30 + (i % 3) * 5),
  duration_minutes: 450 - (i % 4) * 20, efficiency: 0.9, quality: 4, stages: {}, source: i % 2 ? 'manual' : 'health_connect',
  external_id: null, note: i === 0 ? 'Woke once when the dog barked at something outside the window' : '',
}));

const food = (id: number, meal: string, name: string, calories: number, brand = '') => ({
  id, user_id: 'parent', day: day(), meal, name, brand, calories, protein_g: 12, carbs_g: 30, fat_g: 8, fiber_g: 3, sugar_g: 5,
  sodium_mg: 300, servings: 1, serving_grams: 120, barcode: null, recipe_path: null, source: 'manual', external_id: null,
  created_at: at(0, 8), density: null,
});
const ENTRIES = [
  food(1, 'breakfast', 'Oatmeal with blueberries, walnuts and a drizzle of honey', 320, 'Homemade'),
  food(2, 'lunch', 'Turkey sandwich', 450),
  food(3, 'snack', 'Apple', 95),
];

const TARGET = { kind: 'adult_range', min: 2200, max: 2600, bmr: 1750, tdee: 2450, goal: 'maintain', note: 'A range, not a rule.',
  missing: [], deficit_allowed: true, protein_g: 120, carbs_g: 280, fat_g: 80 };

export const NUTRITION = {
  user_id: 'parent', day: day(), entries_count: ENTRIES.length, logged: true,
  totals: { calories: 865, protein_g: 36, carbs_g: 90, fat_g: 24, fiber_g: 9 },
  meals: {
    breakfast: { entries: [ENTRIES[0]], calories: 320 },
    lunch: { entries: [ENTRIES[1]], calories: 450 },
    snack: { entries: [ENTRIES[2]], calories: 95 },
  },
  target: TARGET,
};

const score = (s: number, label: string) => ({ score: s, label, detail: 'Slept close to your need and your resting heart rate is steady.',
  confidence: 'high', missing: [], components: { sleep: 80, hr: 70 } });

const SUMMARY = {
  user_id: 'parent', day: day(), profile: HEALTH_PROFILE, defaults_used: [],
  sleep: { last_night: SLEEP[0], score: score(82, 'Good'), need_minutes: 480, history: SLEEP.slice(0, 7) },
  readiness: score(76, 'Ready'),
  checkin: { id: 1, user_id: 'parent', day: day(), energy: 4, mood: 4, soreness: 2, stress: 2, note: '', updated_at: at(0, 7) },
  nutrition: NUTRITION,
  water: { ml: 1500, goal_ml: 2400, percent: 63, logged: true },
  metrics: {
    resting_hr: { id: 9, user_id: 'parent', metric: 'resting_hr', value: 58, unit: 'bpm', recorded_at: at(0, 6), source: 'health_connect', external_id: null, metadata: {} },
    weight: { id: 10, user_id: 'parent', metric: 'weight', value: 80.5, unit: 'kg', recorded_at: at(-1, 7), source: 'manual', external_id: null, metadata: {} },
  },
  resting_hr_baseline: 60, activity: null, cycle: null, alerts: [],
};

const KINDS = [
  { kind: 'water_days', title: 'Water goal days', unit: 'days', detail: 'Days you reach your water goal', taken: false },
  { kind: 'sleep_nights', title: 'Good sleep nights', unit: 'nights', detail: 'Nights of at least your sleep need', taken: true },
  { kind: 'cook_nights', title: 'Home cooked dinners', unit: 'nights', detail: 'Dinners cooked at home', taken: false },
  { kind: 'step_days', title: 'Step goal days', unit: 'days', detail: 'Days you reach your step goal', taken: false },
];

const CHALLENGES = {
  week_start: day(-3), week_end: day(3), can_create: true, kinds: KINDS,
  challenges: [{
    id: 1, kind: 'sleep_nights', title: 'Good sleep nights', unit: 'nights', detail: 'Nights of at least your sleep need',
    week_start: day(-3), week_end: day(3), target_days: 5, created_by: 'parent', joined: true,
    leaderboard: [
      { user_id: 'parent', count: 3, days: [day(-2), day(-1), day()], reached: false, guideline_goal: false },
      { user_id: LONG_NAME, count: 4, days: [day(-3), day(-2), day(-1), day()], reached: false, guideline_goal: true },
      { user_id: 'sam', count: 1, days: [day()], reached: false, guideline_goal: false },
    ],
  }],
};

const CHEER_KINDS = [
  { kind: 'high_five', emoji: '🙌', label: 'High five' }, { kind: 'proud', emoji: '💪', label: 'Proud of you' },
  { kind: 'keep_going', emoji: '🔥', label: 'Keep going' }, { kind: 'rest', emoji: '😴', label: 'Rest well' },
];
const CHEERS = {
  day: day(), kinds: CHEER_KINDS, can_cheer: ['mom', 'sam', LONG_NAME],
  received: [{ id: 1, from_user: LONG_NAME, to_user: 'parent', day: day(), kind: 'high_five', emoji: '🙌', label: 'High five', created_at: at(0, 9) }],
  sent: [],
};

const GOALS = {
  week_start: day(-3), week_end: day(3), max_goals: 3, kinds: KINDS,
  goals: [{ id: 1, kind: 'water_days', title: 'Water goal days', unit: 'days', detail: 'Days you reach your water goal', target_days: 5, count: 2,
    days: [day(-1), day()], reached: false, guideline_goal: false }],
  suggestion: { kind: 'step_days', target_days: 4, title: 'Step goal days', unit: 'days', reason: 'You reached it three times last week.' },
};

const STREAKS = {
  day: day(), rule: 'One rest day a week keeps a streak.',
  habits: [
    { key: 'logging', title: 'Logging food', detail: 'A meal logged', current: 6, best: 21, rest_days: 1, today_done: true },
    { key: 'checkin', title: 'Check-in', detail: 'How you feel', current: 3, best: 9, rest_days: 0, today_done: true },
    { key: 'water_goal', title: 'Water goal', detail: 'Your water goal reached', current: 0, best: 4, rest_days: 1, today_done: false },
    { key: 'sleep_logged', title: 'Sleep logged', detail: 'A night recorded', current: 14, best: 30, rest_days: 0, today_done: true },
  ],
};

const WEEKLY_METRICS = ['sleep_minutes', 'sleep_score', 'calories', 'water_ml', 'energy', 'mood'];
const WEEKLY = {
  user_id: 'parent', start: day(-6), end: day(), metrics: WEEKLY_METRICS, days_logged: 6,
  averages: { sleep_minutes: 440, sleep_score: 80, calories: 2300, water_ml: 1900, energy: 3.8, mood: 4 },
  last_week: { sleep_minutes: 410, sleep_score: 74, calories: 2450, water_ml: 1700, energy: 3.4, mood: 3.6 },
  best_sleep: { day: day(-2), minutes: 470, score: 88 },
  changes: [{ metric: 'sleep_minutes', this_week: 440, last_week: 410, delta: 30, direction: 'up' }],
  days: Array.from({ length: 7 }, (_, i) => ({ day: day(i - 6), sleep_minutes: 420 + i * 5, sleep_score: 75 + i, calories: 2300, water_ml: 1800, energy: 4, mood: 4 })),
};

const ACTIVE = {
  week_start: day(-3), week_end: day(3), target_minutes: 150, total_minutes: 95, percent: 63,
  days: Array.from({ length: 7 }, (_, i) => ({ day: day(i - 3), minutes: i <= 3 ? 20 + i * 5 : null, sources: ['steps'] })),
  sources: ['steps', 'workouts'], estimate: true, notes: ['Estimated from steps on days without a workout.'], unavailable: [],
};

const FASTING = {
  allowed: true, presets: [12, 16, 18, 24],
  active: { id: 3, started_at: new Date(Date.now() - 5 * 3600_000).toISOString(), ended_at: null, target_hours: 16, elapsed_minutes: 300,
    target_minutes: 960, percent: 31, remaining_minutes: 660, reached: false, elapsed_seconds: 18_000, remaining_seconds: 39_600,
    kind: 'time', kind_name: 'Time-restricted', ends_at: new Date(Date.now() + 11 * 3600_000).toISOString(), days: null, day: null,
    intention: 'Praying for a friend who is in hospital this week', notify_shared: false, prayer_reminders: false },
  recent: [],
};

const STEPS_DAILY = Object.fromEntries(Array.from({ length: 30 }, (_, i) => [day(-i), 6000 + ((i * 1373) % 6000)]));

function stepRanges(range: string) {
  const buckets = Array.from({ length: 7 }, (_, i) => ({ label: `D${i + 1}`, start: day(i - 6), end: day(i - 6), steps: 6000 + i * 700,
    days_missing: 0, days_recorded: 1, complete: i < 6 }));
  const base = { user_id: 'parent', range, label: range === 'D' ? 'Today' : 'Last 7 days', total: 52_000, daily_average: 7400,
    days_recorded: 7, goal: 10_000, baseline: 7000, baseline_min_days: 7, thin: false, has_gaps: false,
    best: { label: 'D7', steps: 10_200 } };
  if (range === 'D') {
    const hourly = [7, 8, 12, 17].map((h) => ({ hour: h, label: `${pad(h)}:00`, steps: h * 60 }));
    return { ...base, buckets: buckets.slice(-1), hourly, peak: hourly[3] };
  }
  return { ...base, buckets };
}

const CYCLE = { applicable: false, enabled: false, is_minor: false, average_cycle_length: null, average_period_length: null,
  guardian_username: null, summary: null, periods: [], logs: [], reason: 'Not tracked for this profile.' };

const EXTRA = new Set(['/api/users/family-directory', '/api/communication/timers', '/api/activity/use', '/api/usage/parent', '/api/resources']);

const DIRECTORY = [
  { username: 'parent', display_name: 'Dad', nextcloud_user: null, avatar: null },
  { username: 'mom', display_name: 'Mom', nextcloud_user: null, avatar: null },
  { username: LONG_NAME, display_name: LONG_NAME, nextcloud_user: null, avatar: null },
  { username: 'sam', display_name: 'Sam', nextcloud_user: null, avatar: null },
];

const TRADITIONS = {
  kinds: [
    { key: 'time', name: 'Time-restricted', era: 'Any time', summary: 'Eat within a window.', rule: 'No food outside the window.',
      pattern: { type: 'hours', hours: 16 }, sources: [{ cite: 'Matthew 6:16-18', note: 'When you fast.' }], scripture: ['Matthew 6:16-18'] },
    { key: 'daniel', name: 'Daniel fast', era: 'Old Testament', summary: 'Vegetables and water for a season.', rule: 'No meat or wine.',
      pattern: { type: 'days', days: 21, daily: 'vegetables and water' }, sources: [{ cite: 'Daniel 10:2-3', note: 'Three full weeks.' }],
      scripture: ['Daniel 10:2-3'], caution: 'Talk to a doctor first if you take medicine with food.' },
  ],
  why: ['To seek God first.'], heart: ['Humility, not show.'], psalms: ['Psalm 35:13'],
  fathers: [{ cite: 'Didache 8', note: 'Wednesdays and Fridays.' }],
  prayer_hours: [{ clock: '09:00', name: 'Third hour', reason: 'Pentecost', scripture: 'Acts 2:15' }],
  helps: ['Drink water.'], today: 'Matthew 6:17',
};

export async function mockHealth(page: Page): Promise<string[]> {
  const unknown: string[] = [];
  await page.route(isServerCall, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    const json = (body: unknown) => route.fulfill({ json: body });
    const health = p.startsWith('/api/health/') || p.startsWith('/api/geo/') || EXTRA.has(p);
    if (!health) return route.fallback();
    if (req.method() !== 'GET') return json({ status: 'ok' });
    switch (p) {
      case '/api/health/profile': return json(HEALTH_PROFILE);
      case '/api/health/summary': return json(SUMMARY);
      case '/api/health/weekly': return json(WEEKLY);
      case '/api/health/challenges': return json(CHALLENGES);
      case '/api/health/sleep': return json({ user_id: 'parent', sessions: SLEEP });
      case '/api/health/sleep/estimate': return json({ estimate: null, devices: 0 });
      case '/api/health/nutrition/day': return json(NUTRITION);
      case '/api/health/food': return json({ user_id: 'parent', entries: ENTRIES });
      case '/api/health/shares': return json({ owner: 'parent', shares: [{ viewer: 'mom', scope: 'sleep' }] });
      case '/api/health/family': return json({ viewer: 'parent', day: day(), people: [
        { user_id: 'mom', scopes: ['sleep'], sleep: { duration_minutes: 470, score: 85, label: 'Good', logged: true } },
        { user_id: LONG_NAME, scopes: ['sleep'], sleep: { duration_minutes: 540, score: 90, label: 'Great', logged: true } },
      ] });
      case '/api/health/cycle': return json(CYCLE);
      case '/api/health/active-minutes': return json(ACTIVE);
      case '/api/health/cheers': return json(CHEERS);
      case '/api/health/goals': return json(GOALS);
      case '/api/health/streaks': return json(STREAKS);
      case '/api/health/fasting': return json(FASTING);
      case '/api/health/fasting/traditions': return json(TRADITIONS);
      case '/api/users/family-directory': return json(DIRECTORY);
      case '/api/communication/timers': return json([]);
      case '/api/resources': return json({ topic: 'fasting', books: [{ title: 'A Made-up Book About Fasting and Prayer Through the Ages', author: 'Test Author' }],
        documents: [], passages: [] });
      case '/api/activity/use': return json({ status: 'SUCCESS', use: { days: 7, window_start: day(-6), has_data: true, total: 42,
        top: [{ label: 'Weather', count: 14 }, { label: 'Turning the kitchen lights on and off again', count: 11 }, { label: 'Timers', count: 9 }] } });
      case '/api/usage/parent': return json({ status: 'SUCCESS', days: Array.from({ length: 7 }, (_, i) => ({ day: day(-i), millis: (90 + i * 7) * 60_000,
        first_use: null, last_use: null, sessions: 30 + i, apps: [{ package: 'com.google.android.youtube', millis: 40 * 60_000 },
          { package: 'com.example.a.very.long.package.name.that.keeps.going', millis: 20 * 60_000 }] })) });
      case '/api/geo/steps': return json({ user_id: 'parent', daily_steps: STEPS_DAILY, today: STEPS_DAILY[day()], goal: 10_000,
        sources: { phone: STEPS_DAILY[day()] }, last_synced: Date.now() / 1000 });
      case '/api/geo/steps/ranges': return json(stepRanges(url.searchParams.get('range') || 'W'));
      case '/api/geo/metrics/catalog': return json({ available: [], unavailable: {} });
      case '/api/geo/stars': return json({ user_id: 'parent', stars: 4, grants: [] });
      case '/api/geo/achievements': return json({ user_id: 'parent', days: 30, achievements: [] });
      case '/api/geo/events': return json({ user_id: 'parent', empty: true, total_events: 0, window_days: 30, groups: [] });
      case '/api/geo/activity/summary': return json({ user_id: 'parent', days: 7, users: [] });
      case '/api/geo/activity/feed': return json({ user_id: 'parent', events: [] });
      case '/api/geo/trends/activity': return json({ user_id: 'parent', days: 7 });
      case '/api/geo/workouts': return json({ workouts: [], total: 0 });
      case '/api/geo/workouts/active': return json({ active: null });
      case '/api/geo/trips': return json({ trips: [], total_trips: 0 });
      case '/api/geo/people': return json({ features: [] });
      default: break;
    }
    if (p.startsWith('/api/health/metrics/')) {
      const metric = p.split('/').pop() ?? '';
      const samples = [0, 1, 2].map((i) => ({ id: 200 + i, user_id: 'parent', metric, value: metric === 'weight' ? 80.5 - i * 0.3 : 58 + i,
        unit: metric === 'weight' ? 'kg' : 'bpm', recorded_at: at(-i, 7), source: i ? 'manual' : 'health_connect', external_id: null, metadata: {} }));
      return json({ user_id: 'parent', metric, unit: samples[0].unit, days: 30, samples, latest: samples[0] });
    }
    unknown.push(`${req.method()} ${p}`);
    return route.fallback();
  });
  return unknown;
}
