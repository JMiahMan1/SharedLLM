import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { controlsOffScreen, expectNoHorizontalOverflow, expectTapTargets, snap } from './fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Admin > People (docs/PROFILE_STATS.md) at every hermetic size: the flags
 * first, request outcomes, devices and collection health, what is used and
 * what is missing, screen time, the Guardian and errors, then the person's
 * profile without the opt-in. The server refuses non-admins (gateway tests);
 * here a member is shown the app's refusal and never asks for the stats.
 */

const ADMIN = { id: 1, username: 'pat', display_name: 'Pat', full_name: 'Pat', role: 'admin', is_admin: true, family_role: 'father' };
const MEMBER = { id: 2, username: 'kit', display_name: 'Kit', full_name: 'Kit', role: 'user', is_admin: false, family_role: 'child' };

const DIRECTORY = [
  { username: 'pat', display_name: 'Pat', nextcloud_user: null, avatar: null, family_role: 'father' },
  { username: 'kit', display_name: 'Kit Longname-Example', nextcloud_user: null, avatar: null, family_role: 'child' },
];

const now = Date.now();
const iso = (ms: number) => new Date(now - ms).toISOString();
const day = (n: number) => new Date(now - n * 86400000).toISOString().slice(0, 10);

const STATS = {
  username: 'kit',
  person: { username: 'kit', display_name: 'Kit Longname-Example' },
  flags: [
    { area: 'bible', level: 'watch', text: 'Bible reading streak has stopped (best was 12 days).' },
    { area: 'screen', level: 'watch', text: 'Screen time is up 48 min a day on last week.' },
    { area: 'jarvis', level: 'fix', text: '2 things asked for that Jarvis cannot do yet.' },
  ],
  profile: { username: 'kit', own: true, person: { username: 'kit', display_name: 'Kit Longname-Example' }, cards: [
    { area: 'bible', state: 'ok', data: { streak_current: 0, streak_longest: 12, days_read: 40, chapters_read: 80, books_read: 2,
      achievements: [], points: 60, quiz_today: null, window: [] } },
    { area: 'garden', state: 'ok', data: { streak_current: 2, streak_longest: 4, care_logs: 9, achievements: [], points: 5 } },
    { area: 'jarvis_use', state: 'unavailable', data: null, reason: 'This part of Jarvis is not answering.' },
  ] },
  sections: {
    voice: { state: 'ok', data: {
      days: 30, total: 214, outcomes: { ok: 190, timeout: 9, error: 4, tool_failure: 8, offline: 3, queued: 12 },
      latency: { buckets: { '<1000': 80, '<2000': 70, '<4000': 40, '<8000': 15, '<15000': 9 }, p50_ms: 2000, p90_ms: 8000 },
      per_day: Array.from({ length: 14 }, (_, i) => ({ day: day(13 - i), total: 15, ok: 13, failed: i % 3 })),
      tools: [{ tool: 'set_oven_temperature_on_the_kitchen_range', failures: 5, calls: 6 }, { tool: 'calendar_read', failures: 3, calls: 40 }],
      slow_hours: [{ hour: 7, slow: 6, total: 20 }, { hour: 19, slow: 4, total: 30 }],
    } },
    usage: { state: 'ok', data: {
      devices: [
        { label: "Kit's phone with a long name", kind: 'phone', model: 'Phone 9', manufacturer: 'Maker', os_version: '15', app_version: '2.1.0', last_seen_at: iso(3600_000) },
        { label: 'Old tablet', kind: 'tablet', app_version: '1.9.0', last_seen_at: iso(9 * 86400000) },
      ],
      features: [{ feature: 'bible', kind: 'app', uses: 40, last_used_at: iso(3600_000) }, { feature: 'recipes', kind: 'app', uses: 3, last_used_at: iso(50 * 86400000) }],
      capability_gaps: [{ capability: 'set_oven', kind: 'assistant', request: 'preheat the oven to four hundred', occurrences: 3, resolved: false }],
      conversations: { total: 12, per_day: [{ day: day(1), count: 5 }, { day: day(0), count: 7 }], features: [{ feature: 'weather', count: 6 }] },
      events: { app_open: 30 }, app_opens_per_day: [{ day: day(0), count: 4 }],
    } },
    collector: { state: 'ok', data: { devices: [{ device_name: "Kit's phone", problems: [{ kind: 'permission', what: 'read_sms' }, { kind: 'worker', what: 'usage_sync', state: 'CANCELLED' }] }], staleness: {}, problem_count: 2 } },
    features: { state: 'ok', data: { since: day(30), counts: { media: 90, bible: 44, game: 20 } } },
    screen: { state: 'ok', data: { days: Array.from({ length: 14 }, (_, i) => ({ day: day(13 - i), minutes: 60 + i * 9, sessions: 10 })) } },
    guardian: { state: 'ok', data: { concerns: 2, levels: { high: 1, low: 1 }, recap_headline: 'A calm week with more reading.' } },
    logs: { state: 'unavailable', reason: 'This part of Jarvis is not answering.' },
  },
};

async function mockApi(page: Page, user: typeof ADMIN | typeof MEMBER) {
  const asked: string[] = [];
  await page.addInitScript((profile) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(profile));
  }, user);
  // The admin shell asks for many things; everything not about this page answers empty.
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/'), async (route: Route) => {
    const p = new URL(route.request().url()).pathname;
    asked.push(p);
    if (p === '/api/users/me') return route.fulfill({ json: user });
    if (p === '/api/users/family-directory') return route.fulfill({ json: DIRECTORY });
    if (p === '/api/profile/admin/kit') return route.fulfill({ json: STATS });
    if (p === '/api/profile/admin/pat') return route.fulfill({ json: { ...STATS, username: 'pat', person: { username: 'pat', display_name: 'Pat' }, flags: [] } });
    if (p === '/api/deploy-status') return route.fulfill({ status: 404, body: '' });
    if (p === '/api/integrations/status') return route.fulfill({ json: { homehub: { enabled: true }, home_assistant: { enabled: true } } });
    if (/\/api\/telemetry\//.test(p)) return route.fulfill({ json: { notifications: [], jobs: [] } });
    if (/\/api\/(config|calendar\/settings)\b/.test(p)) return route.fulfill({ json: { status: 'SUCCESS', settings: {}, config: {} } });
    return route.fulfill({ json: [] });
  });
  return asked;
}

test.describe('Admin > People', () => {
  test('one person, with what needs a look first', async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await mockApi(page, ADMIN);
    await page.goto('/admin/people/kit');
    const view = page.getByTestId('admin-person-stats');
    await expect(view).toBeVisible();
    await expect(page.getByTestId('admin-flags')).toContainText('streak has stopped');
    await expect(page.getByTestId('admin-voice')).toContainText('Did not go through');
    await expect(page.getByTestId('admin-voice')).toContainText('set_oven_temperature_on_the_kitchen_range');
    await expect(page.getByTestId('admin-devices')).toContainText('app 1.9.0');
    await expect(page.getByTestId('admin-collector-problems')).toContainText('Permission off: read sms');
    await expect(page.getByTestId('admin-gaps')).toContainText('set_oven');
    await expect(page.getByTestId('admin-assistant')).toContainText('counted, not read');
    await expect(page.getByTestId('admin-logs-error')).toContainText('not answering');
    await expect(page.getByTestId('admin-person-profile').getByTestId('profile-card-garden')).toBeVisible();

    await expectNoHorizontalOverflow(page);
    await expectTapTargets(page.getByTestId('admin-people-strip'));
    await expectTapTargets(view);
    expect(await controlsOffScreen(page)).toEqual([]);
    const axe = await new AxeBuilder({ page }).include('[data-testid="admin-people"]').analyze();
    expect(axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([]);
    await snap(page, info, 'admin-people');

    // Choosing another person from the strip.
    await page.getByTestId('admin-person-pat').click();
    await expect(page).toHaveURL(/\/admin\/people\/pat$/);
    await expect(page.getByTestId('admin-flags-none')).toContainText('Nothing stands out for Pat');
    expect(errors).toEqual([]);
  });

  test('a member is refused and never asks for the stats', async ({ page }) => {
    const asked = await mockApi(page, MEMBER);
    await page.goto('/admin/people/kit');
    await expect(page.getByTestId('admin-person-stats')).toHaveCount(0);
    expect(asked.filter((p) => p.startsWith('/api/profile/admin'))).toEqual([]);
  });
});
