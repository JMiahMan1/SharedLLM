import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * The family member page (docs/FAMILY_MEMBER_PAGE.md) on desktop and phone,
 * for the father (every section, Devices included) and the mother (every
 * personal section and control, no technical data). Hermetic: every call the
 * page and the shell make is answered here, and the catch-all must stay empty.
 */

type Viewer = 'dad' | 'mom';

const PROFILES: Record<Viewer, Record<string, unknown>> = {
  dad: { id: 1, username: 'dad', display_name: 'Dad', full_name: 'Dad', role: 'user', is_admin: false, family_role: 'father', is_minor: false },
  mom: { id: 2, username: 'mom', display_name: 'Mom', full_name: 'Mom', role: 'user', is_admin: false, family_role: 'mother', is_minor: false },
};

function day(offset = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

const ROSTER = [
  { username: 'dad', display_name: 'Dad', family_role: 'father', mother_username: null, is_minor: false, sharing_locked: false, is_admin: false, avatar: null },
  { username: 'mom', display_name: 'Mom', family_role: 'mother', mother_username: null, is_minor: false, sharing_locked: false, is_admin: false, avatar: null },
  { username: 'emma', display_name: 'Emma', family_role: 'teen', mother_username: 'mom', is_minor: true, sharing_locked: true, is_admin: false, avatar: null },
  { username: 'sam', display_name: 'Sam', family_role: 'child', mother_username: 'mom', is_minor: true, sharing_locked: true, is_admin: false, avatar: null },
];

function status(username: string, technical: boolean) {
  return {
    username, day: day(), rules: { daily_limit_min: 120, bedtime_start: '21:00', bedtime_end: '07:00', app_limits: { 'com.roblox.client': 30 } },
    can_manage: true, technical, ...(technical ? { protected: true, app_approvals: {}, permission_approvals: {} } : {}),
    limit_min: 120, used_min: username === 'sam' ? 95 : 140, bonus_min: 0, remaining_min: username === 'sam' ? 25 : 0,
    bedtime_active: false, school_active: false, paused: false, locked: username !== 'sam', reason: username === 'sam' ? null : 'limit',
    until: null, resets_at: `${day(1)}T00:00:00`, blocked_apps: [], app_minutes_left: {},
    next_lock_at: new Date(Date.now() + 130 * 60000).toISOString(), next_lock_reason: 'bedtime',
  };
}

function usage(username: string) {
  return {
    status: 'SUCCESS',
    days: Array.from({ length: 7 }, (_, i) => ({
      day: day(-i),
      millis: (username === 'sam' ? 95 - i * 6 + (i === 3 ? 60 : 0) : 140) * 60000,
      first_use: new Date().setHours(7, 5, 0, 0),
      last_use: Date.now() - 3 * 60000,
      sessions: 20 + i,
      apps: [
        { package: 'com.google.android.youtube', millis: 50 * 60000, last_use: Date.now() - 3 * 60000 },
        { package: 'com.roblox.client', millis: 25 * 60000, last_use: Date.now() - 60 * 60000 },
        { package: 'com.spotify.music', millis: 20 * 60000, last_use: Date.now() - 90 * 60000 },
      ],
    })),
  };
}

const ACTIVITY = {
  status: 'SUCCESS',
  events: [
    { user: 'sam', feature: 'media', item: 'Lecrae — Blessings', action: 'play', at: new Date(Date.now() - 20 * 60000).toISOString() },
    { user: 'sam', feature: 'web', item: 'khanacademy.org', action: 'visit', at: new Date(Date.now() - 50 * 60000).toISOString(), detail: { visits: 4 } },
    { user: 'sam', feature: 'web', item: 'tiktok.com', action: 'blocked', at: new Date(Date.now() - 70 * 60000).toISOString(), detail: { blocked: 1 } },
    { user: 'sam', feature: 'bible', item: 'Psalm 23', action: 'read', at: new Date(Date.now() - 26 * 3600000).toISOString() },
  ],
};

const DEVICES = {
  username: 'sam',
  devices: [{
    device_key: 'sam-phone-000000000001', kind: 'phone', label: "Sam's Pixel", model: 'Pixel 7a', manufacturer: 'Google',
    os_version: '14', os_build: 'AP2A', app_version: '1.9.0', app_build: '512', last_ip_address: '10.0.0.42',
    last_seen_at: new Date(Date.now() - 5 * 60000).toISOString(), first_seen_at: '2026-09-01T10:00:00',
    capabilities: { guard: { adminActive: true, deviceOwner: false, guardEnabled: true, webFilterAllowed: true, webFilterActive: true, usageAccess: true, reportedAt: new Date().toISOString() } },
    battery: { at: new Date().toISOString(), pct: 64, usb: false },
  }],
  tamper: [{ event: 'guard_tamper', at: new Date(Date.now() - 2 * 86400000).toISOString(), extra: { event: 'guard_disabled' }, device_key: 'sam-phone-000000000001' }],
  offline_code: { dad: true, mom: false },
};

async function mockApi(page: Page, viewer: Viewer) {
  const unmocked: string[] = [];
  page.on('pageerror', (e) => console.log(`[pageerror] ${e.message}`));
  await page.addInitScript((profile) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(profile));
  }, PROFILES[viewer]);
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    const member = p.match(/^\/api\/(?:screen-time|usage|guardian\/(?:recap|guidance|concerns|journal|activity))\/([^/]+)/)?.[1];

    // The shell's own calls (theme, notices, calls, media token, activity beacon).
    if (p === '/api/users/me') return json(PROFILES[viewer]);
    if (p === '/api/users/me/theme') return json({ theme: 'dark' });
    if (p === '/api/telemetry/notifications') return json({ notifications: [] });
    if (p === '/api/communication/calls') return json({ calls: [] });
    if (p === '/api/media/token') return json({ token: 'test-media-token', expires_at: 4102444800, user: viewer });
    if (p === '/api/activity') return json({ status: 'SUCCESS' });
    // The page's own.
    if (p === '/api/users/family') return json(ROSTER);
    if (p === '/api/users/family-directory') return json([]);
    if (p === '/api/users/location/all') {
      return json({ sam: { latitude: 33.4484, longitude: -112.074, accuracy: 10, battery: viewer === 'dad' ? 64 : null, timestamp: Date.now() / 1000 - 120 } });
    }
    if (p === '/api/geo/zones') {
      return json({ features: [{ properties: { entity_id: 'zone.school', friendly_name: 'School', latitude: 33.4484, longitude: -112.074, radius: 200, important: true }, geometry: { coordinates: [-112.074, 33.4484] } }] });
    }
    if (p === '/api/screen-time/requests') {
      return json({ requests: url.searchParams.get('status') === 'pending' ? [{ id: 7, username: 'sam', day: day(), minutes: 30, note: 'finish the level', status: 'pending', granted_minutes: 0, created_at: new Date().toISOString() }] : [] });
    }
    if (p === '/api/guard/requests') {
      return json({ requests: url.searchParams.get('status') === 'pending' ? [{ id: 3, username: 'sam', kind: 'app', target: 'com.mojang.minecraftpe', label: 'Minecraft', note: '', status: 'pending', until: null, decided_by: null, created_at: new Date().toISOString(), decided_at: null }] : [] });
    }
    if (p === '/api/guard/offline-code') return json({ set: viewer === 'dad', updated_at: null, can_set: true });
    if (member && p.endsWith('/status')) return json(status(member, viewer === 'dad'));
    if (p.startsWith('/api/usage/')) return json(usage(member || ''));
    if (p.startsWith('/api/guardian/concerns/')) {
      return json({ status: 'SUCCESS', concerns: [{ category: 'bullying', label: 'Bullying', level: 'high', excerpt: 'nobody likes you', feature: 'sms', at: new Date().toISOString() }] });
    }
    if (p.startsWith('/api/guardian/guidance/')) {
      return json({ status: 'SUCCESS', week: { start: day(-6), end: day() }, prompts: [{ signal: 'bullying', title: 'Unkind words', ref: 'Psalm 139:14', verse: 'I will praise thee', prayer: 'Pray for Sam.', starter: 'Has anyone said anything to you lately that hurt?' }] });
    }
    if (p.startsWith('/api/guardian/recap/')) {
      return json({ status: 'SUCCESS', recap: { week: { start: day(-6), end: day() }, has_data: false, screen: null, top_apps: [], media: [], web: { top: [], blocked: [] }, comms: { calls: 0, call_contacts: 0, messages: 0, message_contacts: 0 }, faith: { bible_days: 0 }, concerns: [], highlights: [] } });
    }
    if (p.startsWith('/api/guardian/journal/')) return json({ notes: [] });
    if (p.startsWith('/api/guardian/activity/')) return json(ACTIVITY);
    if (p.startsWith('/api/recommendation-lanes/')) return json({ username: member, lanes: { music: true, video: true, podcast: true, book: true, game: true, activity: true }, can_manage: true });
    if (p === '/api/health/family') {
      return json({ viewer, day: url.searchParams.get('day'), people: [{ user_id: 'sam', scopes: ['sleep'], sleep: { duration_minutes: 540, score: 82, label: 'Good', logged: true } }] });
    }
    if (p === '/api/geo/steps') return json({ user_id: 'sam', daily_steps: { [day()]: 6400, [day(-1)]: 9100 }, today: 6400, goal: 10000 });
    if (p === '/api/geo/trips') return json({ trips: [], total_trips: 0 });
    if (/^\/api\/family\/member\/[^/]+\/devices$/.test(p)) {
      // The server's rule: the father and admins only.
      return viewer === 'dad' ? json(DEVICES) : json({ detail: 'Only the father or an admin can see device details' }, 403);
    }

    unmocked.push(`${route.request().method()} ${p}`);
    return route.fulfill({ json: [] });
  });
  return { unmocked };
}

async function overflow(page: Page) {
  return page.evaluate(() => ({ doc: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
}

test.describe('family member page', () => {
  test('the father sees every section, Devices included', async ({ page }, info) => {
    const { unmocked } = await mockApi(page, 'dad');
    await page.goto('/guardian?child=sam');
    await expect(page).toHaveURL(/\/family\/member\/sam$/);
    await expect(page.getByTestId('member-header')).toContainText('Sam');
    await expect(page.getByTestId('member-today-left')).toContainText('25 min left today');
    await expect(page.getByTestId('member-next-routine')).toContainText('Bedtime in 2 h');
    await expect(page.getByTestId('member-where')).toContainText('School');
    await expect(page.getByTestId('open-requests')).toContainText('Asks for 30 more minutes');
    await expect(page.getByTestId('open-requests')).toContainText('Installed Minecraft');
    await expect(page.getByTestId('concern')).toContainText('What to say');
    await expect(page.getByTestId('member-week-chart-summary')).toContainText('7 of 7 days reported');
    await expect(page.getByTestId('member-week-chart-summary')).toContainText('Over the limit on 1');

    await page.screenshot({ path: info.outputPath('overview.png'), fullPage: true });

    const mobile = info.project.name.includes('mobile');
    const sectionButton = (id: string) => page.getByTestId(`member-${mobile ? 'tab' : 'rail'}-${id}`);
    await expect(sectionButton('devices')).toBeVisible();

    await sectionButton('devices').click();
    await expect(page.getByTestId('member-device-sam-phone-000000000001')).toContainText('1.9.0 (build 512)');
    await expect(page.getByTestId('member-device-guard')).toContainText('Accessibility screen guard');
    await expect(page.getByTestId('member-tamper')).toContainText("screen guard was turned off");
    await page.screenshot({ path: info.outputPath('devices.png'), fullPage: true });

    await sectionButton('time').click();
    await expect(page.getByTestId('weekly-schedule')).toBeVisible();
    await expect(page.getByTestId('member-heatmap-grid')).toBeVisible();
    await page.screenshot({ path: info.outputPath('time.png'), fullPage: true });

    await sectionButton('activity').click();
    await expect(page.getByTestId('member-timeline-list')).toContainText('Lecrae');

    await sectionButton('health').click();
    // Fourteen nights are fourteen reads of the sharing rules.
    await expect(page.getByTestId('member-sleep-chart-summary')).toContainText('14 of 14 days reported', { timeout: 15_000 });
    await page.screenshot({ path: info.outputPath('health.png'), fullPage: true });

    if (mobile) {
      // 44px touch targets on the section tabs and quick actions.
      for (const id of ['member-pause', 'member-bonus', 'member-lock', 'member-tab-overview']) {
        const box = await page.getByTestId(id).boundingBox();
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      }
    }
    const o = await overflow(page);
    expect(o.doc).toBeLessThanOrEqual(o.client + 1);
    expect(unmocked).toEqual([]);
  });

  test('the mother sees every personal section and control, no technical data', async ({ page }, info) => {
    const { unmocked } = await mockApi(page, 'mom');
    const devicesCalls: string[] = [];
    page.on('request', (r) => { if (/\/api\/family\/member\/.*\/devices/.test(r.url())) devicesCalls.push(r.url()); });
    await page.goto('/family/member/sam?section=devices');
    await expect(page.getByTestId('member-header')).toContainText('Sam');
    // A Devices link falls back to the overview for her; the tab is not there.
    await expect(page.getByTestId('member-panel-overview')).toBeVisible();
    const mobile = info.project.name.includes('mobile');
    await expect(page.getByTestId(`member-${mobile ? 'tab' : 'rail'}-overview`)).toBeVisible();
    await expect(page.getByTestId('member-tab-devices')).toHaveCount(0);
    await expect(page.getByTestId('member-rail-devices')).toHaveCount(0);

    // Every control is hers too.
    await expect(page.getByTestId('member-pause')).toBeVisible();
    await expect(page.getByTestId('open-requests')).toContainText('Installed Minecraft');
    await page.getByTestId(`member-${mobile ? 'tab' : 'rail'}-apps`).click();
    await expect(page.getByTestId('app-controls')).toContainText('YouTube');
    await expect(page.getByTestId('web-filter-controls')).toBeVisible();
    await expect(page.getByTestId('member-recent-web')).toContainText('tiktok.com');

    expect(devicesCalls).toEqual([]);
    const o = await overflow(page);
    expect(o.doc).toBeLessThanOrEqual(o.client + 1);
    expect(unmocked).toEqual([]);
  });
});
