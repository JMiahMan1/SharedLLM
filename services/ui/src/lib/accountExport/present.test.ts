import { describe, expect, it } from 'vitest';
import kinds from './kinds.json';
import { presentExport, type PresentedService } from './present';

/** A made-up person's export: one or more rows of every kind, ids chosen to be easy to spot. */
const ID = 987654;
const sample: Record<string, Record<string, Record<string, unknown>[]>> = {
  identity: {
    profile: [{ id: ID, username: 'sam', display_name: 'Sam Example', family_role: 'father', birth_date: null, nextcloud_url: 'https://cloud.example.test', nextcloud_user: 'sam', skylight_enabled: true, preferred_tts_voice: 'af_heart' }],
    avatar_image: [{ username: 'sam', mime: 'image/png', data: { $base64: 'AAAA' }, updated_at: '2026-01-05T10:00:00' }],
    theme: [{ username: 'sam', data: '{"theme_id":"forest","packs":[]}' }],
    weather: [{ username: 'sam', data: '{"units":"metric","follow":true,"location":{"name":"Home farm","latitude":1,"longitude":2}}' }],
    calendar_settings: [{ username: 'sam', data: '{"default_integration":"nextcloud"}' }],
    notification_preferences: [{ username: 'sam', data: '{"media":false,"weather":true}' }],
    activity_sharing: [{ username: 'sam', data: '{"steps":true,"location":false}' }],
    dashboard_widgets: [{ id: ID, username: 'sam', widget_key: 'weather_card', visibility: 'visible', order_index: 1, size: 'medium', is_pinned: true, pinned_devices: '[]', config: '{}', updated_at: 1000 }],
    workspace_sessions: [{ id: ID, username: 'sam', workspace_id: 'ws-abc', data: '{"currentPath":"src/app.py","tabs":[{"path":"a"},{"path":"b"}]}', updated_at: '2026-01-06T09:00:00' }],
    workspace_chats: [{ id: 'chat-xyz', workspace_id: 'ws-abc', username: 'sam', title: 'Fix the build', created_at: '2026-01-06T09:00:00', updated_at: '2026-01-06T09:05:00' }],
    workspace_chat_messages: [
      { id: ID, chat_id: 'chat-xyz', role: 'user', parts: '[{"type":"text","text":"Why does the build fail?"}]', meta: '{}', created_at: '2026-01-06T09:00:00' },
      { id: ID + 1, chat_id: 'chat-xyz', role: 'assistant', parts: '[{"type":"reasoning","text":"secret thinking"},{"type":"tool","name":"bash"},{"type":"text","text":"A missing import."}]', meta: '{}', created_at: '2026-01-06T09:01:00' },
    ],
    assistant_conversations: [{ id: ID, username: 'sam', turns: '[{"role":"user","text":"Turn on the porch light"},{"role":"assistant","text":"Done, the porch light is on."}]', features: '["lights"]', at: '2026-01-07T20:00:00' }],
    feature_usage: [{ username: 'sam', feature: 'bible_reader', kind: 'page', uses: 12, first_used_at: '2026-01-01T00:00:00', last_used_at: '2026-01-07T00:00:00' }],
    screen_rules: [{ username: 'sam', data: '{"daily_limit_min":120,"bedtime_start":"21:00","paused":false}', updated_by: 'mom', updated_at: '2026-01-02T00:00:00' }],
    recommendation_lanes: [{ username: 'sam', data: '{"music":true}', updated_by: 'mom', updated_at: '2026-01-02T00:00:00' }],
    screen_time_requests: [{ id: ID, username: 'sam', day: '2026-01-03', minutes: 30, note: 'Homework video', status: 'approved', granted_minutes: 30, decided_by: 'mom', created_at: '2026-01-03T16:00:00', decided_at: null }],
    guard_requests: [{ id: ID, username: 'sam', kind: 'app', target: 'com.example.game', label: 'Example Game', note: '', status: 'pending', until: null, decided_by: null, created_at: '2026-01-04T10:00:00', decided_at: null }],
    unlock_requests: [{ id: ID, username: 'sam', device_kind: 'windows-pc', lock_reason: 'bedtime', note: 'homework', minutes_asked: 30, status: 'approved', minutes_granted: 30, until: '2026-01-04T21:30:00', decided_by: 'pat', created_at: '2026-01-04T21:00:00', expires_at: '2026-01-04T21:10:00', decided_at: '2026-01-04T21:01:00', delivered_at: '2026-01-04T21:01:05' }],
    chore_checks: [{ id: ID, username: 'sam', day: '2026-01-04', chore_id: 'c-1', title: 'Feed the chickens', status: 'approved', decided_by: 'mom', created_at: '2026-01-04T08:00:00', decided_at: null }],
    guardian_notes_written: [{ id: ID, author: 'sam', child: 'kid', text: 'Talked about kindness.', shared: true, created_at: '2026-01-05T19:00:00' }],
    account_history: [{ id: ID, at: '2026-01-05T19:00:00', actor: 'sam', actor_kind: 'user', action: 'user.update', target: 'sam', changes: '[{"field":"display_name","change":"changed"}]', source: 'PUT /api/users/me', note: null }],
  },
  health: {
    profile: [{ user_id: 'sam', display_name: 'Sam', birth_year: 1985, height_cm: 180, activity_level: 'moderate', goal: 'maintain', units: 'metric', kid_mode: false, sleep_need_minutes: 480, water_goal_ml: 2000, updated_at: '2026-01-01T00:00:00' }],
    preferences: [{ user_id: 'sam', metrics_json: '["weight"]', updated_at: '2026-01-01T00:00:00' }],
    metrics: Array.from({ length: 12 }, (_, i) => ({ id: ID + i, user_id: 'sam', metric: 'weight', value: 70 + i / 10, unit: 'kg', recorded_at: `2026-0${(i % 3) + 1}-0${(i % 9) + 1}T07:00:00`, source: 'manual', external_id: 'x', metadata_json: '{}' })),
    sleep: [{ id: ID, user_id: 'sam', night: '2026-01-02', bedtime: '2026-01-02T22:30:00', wake_time: '2026-01-03T06:15:00', duration_minutes: 465, efficiency: 0.91, quality: 'good', stages_json: '{}', source: 'watch', external_id: 'e', note: '' }],
    checkins: [{ id: ID, user_id: 'sam', day: '2026-01-02', energy: 4, mood: 5, soreness: 1, stress: 2, note: 'Felt great', updated_at: '2026-01-02T08:00:00' }],
    food: [{ id: ID, user_id: 'sam', day: '2026-01-02', meal: 'breakfast', name: 'Oatmeal', calories: 150, protein_g: 5, carbs_g: 27, fat_g: 3, servings: 2, source: 'manual' }],
    fasting: [{ id: ID, user_id: 'sam', started_at: '2026-01-02T20:00:00', ended_at: '2026-01-03T12:00:00', target_hours: 16, kind: 'intermittent', intention: 'Prayer for a friend', notify_shared: false }],
    goals: [{ id: ID, user_id: 'sam', week_start: '2026-01-05', kind: 'exercise', target_days: 4 }],
    cycle_settings: [{ user_id: 'sam', enabled: true, average_cycle_length: 28, average_period_length: 5, guardian_username: null }],
    cycle_periods: [{ id: ID, user_id: 'sam', start_day: '2026-01-10', end_day: '2026-01-14', flow: 'medium', note: '', source: 'manual', external_id: null }],
    cycle_logs: [{ id: ID, user_id: 'sam', day: '2026-01-11', symptoms_json: '["cramps","headache"]', mood: 'calm', energy: 'low', bbt_c: null, ovulation_test: null, note: '' }],
    recipe_ratings: [{ id: ID, user_id: 'sam', recipe: 'Recipes/Chicken Pot Pie.md', stars: 4, updated_at: '2026-01-08T00:00:00' }],
    recipes_made: [{ id: ID, user_id: 'sam', recipe: 'Recipes/Chicken Pot Pie.md', day: '2026-01-08' }],
    shares_given: [{ id: ID, owner: 'sam', viewer: 'mom', scope: 'weight', created_at: '2026-01-01T00:00:00' }],
    cheers_sent: [{ id: ID, from_user: 'sam', to_user: 'mom', day: '2026-01-03', kind: 'steps' }],
    cheers_received: [{ id: ID, from_user: 'mom', to_user: 'sam', day: '2026-01-03', kind: 'sleep' }],
    challenges_joined: [{ id: ID, challenge_id: 42, user_id: 'sam', joined_at: '2026-01-04T00:00:00' }],
  },
  bible: {
    reading_state: [{ username: 'sam', last_book: 'John', last_chapter: 3, last_verse: 16, last_read_at: '2026-01-09T06:00:00', default_version: 'kjv', layout: 'flow', reminder_time: '06:30', show_notes: true }],
    marks: [
      { id: ID, username: 'sam', version_code: 'kjv', osis: 'John', chapter: 3, verse_start: 16, verse_end: 17, kind: 'highlight', color: 'yellow', note_path: 'Notes/x.md', note_preview: 'For God so loved', created_at: '2026-01-09T06:00:00', updated_at: '2026-01-09T06:00:00' },
      { id: ID + 1, username: 'sam', version_code: 'kjv', osis: 'Ps', chapter: 23, verse_start: 1, verse_end: null, kind: 'bookmark', color: '', note_path: '', note_preview: '', created_at: '2026-01-08T06:00:00', updated_at: '2026-01-08T06:00:00' },
    ],
    memory_verses: [{ id: ID, username: 'sam', ref: 'Philippians 4:13', osis: 'Phil', chapter: 4, verse_start: 13, verse_end: 13, level: 3, due_on: '2026-01-12', created_at: '2026-01-01T00:00:00' }],
    plan_enrollments: [{ id: ID, username: 'sam', plan_id: 'psalms_in_30_days', started_on: '2026-01-01', completed_days: '1,2,3', finished_on: '', active: true, group_id: 7 }],
    plan_reflections: [{ id: ID, username: 'sam', plan_id: 'psalms_in_30_days', day: 2, text: 'He restores my soul.', updated_at: '2026-01-02T07:00:00' }],
    reading_events: [{ id: ID, username: 'sam', kind: 'chapter_read', ref: 'John 3', day: '2026-01-09', value: 1 }],
    achievements: [{ username: 'sam', achievement_id: 'first_chapter', points: 10, earned_on: '2026-01-09' }],
    game_scores: [{ id: ID, username: 'sam', mode: 'verse_match', day: '2026-01-09', seed: 's', version_code: 'kjv', correct: 8, total: 10, duration_ms: 95000, counted: true, answers: '[1,2]', created_at: '2026-01-09T08:00:00' }],
    study_sessions: [{ id: ID, username: 'sam', title: 'Nicodemus', reference: 'John 3', version: 'kjv', created_at: '2026-01-09T08:00:00', updated_at: '2026-01-09T08:10:00' }],
    study_session_turns: [{ id: ID + 5, session_id: ID, question: 'Who was Nicodemus?', answer: 'A Pharisee and a ruler of the Jews.', references: 'John 3:1', created_at: '2026-01-09T08:01:00' }],
    plan_groups_joined: [{ id: ID, group_id: 7, username: 'sam', status: 'active', invited_by: 'mom', joined_at: '2026-01-02T00:00:00' }],
  },
  geo: {
    places: [{ id: 'pl1', owner: 'sam', name: 'Gym', scope: 'user', radius: 120, lat: 1, lon: 2, important: false, notify: [], created_at: 1767225600 }],
    place_reminders: [{ id: 'r1', zone: 'zone.home', zone_name: 'Home', on: 'arrive', title: 'Take out the trash', created_at: 1767225600 }],
    trips: [{ id: 't1', user_id: 'sam', start_time: 1767600000, end_time: 1767601800, distance_miles: 12.4, duration_seconds: 1800, activity_type: 'driving', start_location: { name: 'Home', latitude: 1, longitude: 2 }, end_location: { name: 'Corner Cafe', latitude: 3, longitude: 4 }, vehicle_name: 'Family van', is_shared: false }],
    workouts: [{ id: 'w1', user_id: 'sam', activity_type: 'walking', start_time: 1767700000, end_time: 1767702400, duration_seconds: 2400, distance_miles: 1.9, steps: 4200, auto_detected: true }],
    daily_steps: [{ day: '2026-01-05', steps: '8000' }],
    goals: [{ goal: 'daily_steps_goal', value: '9000' }],
    achievements: [{ achievement_id: 'first_mile', earned_on: '2026-01-05' }],
    visits: [{ spot: '39.97,-105.32', stays: '[{"t":1},{"t":2}]' }],
    location_history: [{ t: 1767600000, lat: 39.97251, lon: -105.32571, acc: 5 }],
  },
};

function strings(presented: PresentedService[]): string[] {
  const out: string[] = [];
  const walk = (v: unknown) => {
    if (typeof v === 'string') out.push(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === 'object') Object.entries(v).forEach(([k, x]) => { if (k !== 'name' && k !== 'service' && k !== 'swatch') walk(x); });
  };
  walk(presented);
  return out;
}

const MERGED: Record<string, string> = { workspace_chat_messages: 'workspace_chats', study_session_turns: 'study_sessions' };

describe('presenting an export', () => {
  const presented = presentExport(sample, { locale: 'en-GB' });
  const find = (service: string, name: string) => presented.find((s) => s.service === service)?.collections.find((c) => c.name === name);
  const firstItem = (service: string, name: string) => find(service, name)!.groups[0].items[0];

  it('has a formatter for every kind the services export', () => {
    const missing: string[] = [];
    for (const [service, names] of Object.entries(kinds as Record<string, string[]>)) {
      for (const name of names) {
        expect(sample[service]?.[name], `the sample has no ${service}.${name}`).toBeTruthy();
        if (MERGED[name]) continue;
        const c = find(service, name);
        if (!c || !c.formatted) missing.push(`${service}.${name}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it('never shows ids, keys, field names or raw flags', () => {
    const text = strings(presented).join('\n');
    expect(text).not.toContain(String(ID));
    expect(text).not.toMatch(/\b(true|false|null|undefined|NaN)\b/);
    expect(text).not.toMatch(/\b[a-z]+_[a-z_]+\b/);
    expect(text).not.toContain('chat-xyz');
    expect(text).not.toContain('ws-abc');
    expect(text).not.toContain('{');
    expect(text).not.toContain('secret thinking');
  });

  it('reads like the app', () => {
    const weights = find('health', 'metrics')!;
    expect(weights.groups.map((g) => g.label)).toEqual(['March 2026', 'February 2026', 'January 2026']);
    expect(weights.groups.reduce((n, g) => n + g.count, 0)).toBe(12);
    expect(weights.groups[2].items.map((i) => i.title)).toContain('Weight 70 kg');
    expect(weights.groups[2].items.find((i) => i.title === 'Weight 70 kg')?.subtitle).toBe('1 Jan 2026, 7:00');
    expect(firstItem('health', 'sleep').title).toBe('7 h 45 min, night of 2 Jan 2026');
    expect(firstItem('health', 'sleep').subtitle).toBe('22:30 to 6:15 · 91% efficient · Good quality');
    const highlight = find('bible', 'marks')!.groups[0].items[0];
    expect(highlight.title).toBe('John 3:16-17');
    expect(highlight.subtitle).toBe('Yellow highlight · KJV · 9 Jan 2026');
    expect(highlight.swatch).toBe('#facc15');
    expect(find('bible', 'marks')!.groups[0].items[1].title).toBe('Psalms 23:1');
    expect(firstItem('geo', 'trips').title).toBe('Home to Corner Cafe');
    expect(firstItem('geo', 'trips').subtitle).toContain('20 km');
    expect(firstItem('health', 'food').subtitle).toBe('Breakfast · 2 Jan 2026 · 300 kcal · 2 servings');
    expect(firstItem('health', 'recipe_ratings')).toMatchObject({ title: 'Chicken Pot Pie', subtitle: '★★★★☆' });
  });

  it('shows conversations as transcripts, with tools named and thinking left out', () => {
    expect(firstItem('identity', 'assistant_conversations').transcript).toEqual([
      { who: 'me', text: 'Turn on the porch light' },
      { who: 'jarvis', text: 'Done, the porch light is on.' },
    ]);
    const chat = firstItem('identity', 'workspace_chats');
    expect(chat.transcript?.map((t) => [t.who, t.text])).toEqual([
      ['me', 'Why does the build fail?'],
      ['jarvis', 'A missing import.\n\n(used a tool)'],
    ]);
    expect(firstItem('bible', 'study_sessions').transcript?.map((t) => t.who)).toEqual(['me', 'jarvis']);
  });

  it('follows the locale and the unit choice', () => {
    const us = presentExport({ geo: sample.geo, health: { ...sample.health, profile: [{ ...sample.health.profile[0], units: 'imperial' }] } }, { locale: 'en-US' });
    const trip = us.find((s) => s.service === 'geo')!.collections.find((c) => c.name === 'trips')!.groups[0].items[0];
    expect(trip.subtitle).toContain('12.4 mi');
    const weight = us.find((s) => s.service === 'health')!.collections.find((c) => c.name === 'metrics')!.groups[2].items.find((i) => i.title.startsWith('Weight 154.3'));
    expect(weight?.subtitle).toBe('Jan 1, 2026, 7:00 AM');
  });

  it('shows an unknown kind as a tidy card: humanised labels, ids hidden', () => {
    const out = presentExport({ health: { blood_tests: [{ id: ID, user_id: 'sam', test_name: 'Iron', taken_on: '2026-01-02', fasting_required: true, lab_ref: 'L-1' }] } }, { locale: 'en-GB' });
    const c = out[0].collections[0];
    expect(c.formatted).toBe(false);
    expect(c.label).toBe('Blood tests');
    expect(c.groups[0].items[0].facts).toEqual([
      { label: 'Test name', value: 'Iron' },
      { label: 'Taken on', value: '2 Jan 2026' },
      { label: 'Fasting required', value: 'Yes' },
      { label: 'Lab ref', value: 'L-1' },
    ]);
  });

  it('runs from its own source text, as the encrypted page runs it', () => {
    const fromSource = new Function(`return (${presentExport.toString()});`)() as typeof presentExport;
    expect(fromSource(sample, { locale: 'en-GB' })).toEqual(presented);
  });
});

describe('the encrypted page uses the same formatting', () => {
  it('embeds presentExport and it still runs after a production minify', async () => {
    const { PAGE_PRESENTER, buildEncryptedPage } = await import('./page');
    const fromPage = new Function(`${PAGE_PRESENTER}; return jarvisPresentExport;`)() as typeof presentExport;
    const expected = presentExport(sample, { locale: 'en-GB' });
    expect(fromPage(sample, { locale: 'en-GB' })).toEqual(expected);
    expect(buildEncryptedPage(new Uint8Array([1, 2, 3]))).toContain('jarvisPresentExport');

    // The app's build minifies with esbuild: the function must survive it on its own.
    const { readFile } = await import('node:fs/promises');
    const { transform } = await import('esbuild');
    const source = await readFile('src/lib/accountExport/present.ts', 'utf8');
    const built = await transform(source, { loader: 'ts', minify: true, format: 'esm', target: 'es2020' });
    const mod = await import(/* @vite-ignore */ `data:text/javascript;base64,${Buffer.from(built.code).toString('base64')}`);
    const minified = new Function(`return (${(mod.presentExport as typeof presentExport).toString()});`)() as typeof presentExport;
    expect(minified(sample, { locale: 'en-GB' })).toEqual(expected);
  });
});
