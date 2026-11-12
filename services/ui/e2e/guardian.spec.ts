import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { isServerCall } from './fixtures/serverCalls';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The Guardian page (/guardian) for a father, a mother and a child, on desktop and phone. Hermetic:
 * every call is answered here and the catch-all must stay empty. The phone checks are the ones that
 * went wrong before: every area at once was a dozen screens long, the area tabs ran off the screen and
 * cards showed through above the sticky bar.
 */

type Viewer = 'dad' | 'mom' | 'sam';

const PROFILES: Record<Viewer, Record<string, unknown>> = {
  dad: { id: 1, username: 'dad', display_name: 'Dad', full_name: 'Dad', role: 'user', is_admin: false, family_role: 'father', is_minor: false },
  mom: { id: 2, username: 'mom', display_name: 'Mom', full_name: 'Mom', role: 'user', is_admin: false, family_role: 'mother', is_minor: false },
  sam: { id: 4, username: 'sam', display_name: 'Sam', full_name: 'Sam', role: 'user', is_admin: false, family_role: 'child', is_minor: true },
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
    username, day: day(), rules: {
      daily_limit_min: 120, bedtime_start: '21:00', bedtime_end: '07:00', app_limits: { 'com.roblox.client': 30 },
      earn: { steps_every: 1000, steps_min: 5, chore_min: 10, max_min: 60 }, chores_first: true,
    },
    earned_min: 30, banked_min: 15, chores_first: true, chores_left: 0,
    can_manage: true, technical, ...(technical ? { protected: true, app_approvals: {}, permission_approvals: {} } : {}),
    limit_min: 120, used_min: username === 'sam' ? 95 : 140, bonus_min: 0, remaining_min: username === 'sam' ? 25 : 0,
    bedtime_active: false, school_active: false, paused: false, locked: username !== 'sam', reason: username === 'sam' ? null : 'limit',
    until: null, resets_at: `${day(1)}T00:00:00`, blocked_apps: [], app_minutes_left: {},
    next_lock_at: new Date(Date.now() + 130 * 60000).toISOString(), next_lock_reason: 'bedtime',
    locked_sections: ['chat', 'bible'],
    locked_section_choices: [
      { id: 'chat', label: 'Chat', core: true }, { id: 'calls', label: 'Calls' }, { id: 'devices', label: 'Lights and the TV remote' },
      { id: 'bible', label: 'Bible' }, { id: 'music', label: 'Music' },
    ],
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
    { user: 'sam', feature: 'media', item: 'Lecrae - Blessings', action: 'play', at: new Date(Date.now() - 20 * 60000).toISOString() },
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
  await page.route(isServerCall, async (route: Route) => {
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
    // The Time section's chores card (docs/ACHIEVEMENTS.md, "Managing chores"): Skylight, for parents.
    if (p === '/api/integrations/skylight/chores') {
      return json({ status: 'SUCCESS', chores: [{ id: '50', title: 'Feed the chickens', completed: false, assignees: ['Sam'], recurrence: 'RRULE:FREQ=DAILY' }] });
    }
    // Ticked-off chores waiting on a parent's OK (docs/HEALTH_HUB.md, "Earning time and chores first").
    if (p === '/api/screen-time/chore-checks') {
      return json({ checks: [{ id: 4, username: 'sam', day: day(), chore_id: '51', title: 'Take out the trash', status: 'pending', created_at: new Date().toISOString() }] });
    }
    if (p === '/api/guardian/my-parents') return json({ parents: ['dad', 'mom'] });
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

test.describe('guardian page', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a parent: one area at a time on a phone, every control reachable', async ({ page }, info) => {
    const { unmocked } = await mockApi(page, 'dad');
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('/guardian');
    const mobile = info.project.name.includes('mobile') || (page.viewportSize()?.width ?? 1440) < 768;
    await expect(page.getByTestId('guardian-child-status')).toContainText('Emma');
    // A phone opens on the overview; a desktop shows every area.
    await expect(page.getByRole('tab', { name: mobile ? 'Overview & Alerts' : 'All areas' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('member-concerns')).toContainText('Bullying');
    if (mobile) await expect(page.getByTestId('guardian-notification-locks-card')).toHaveCount(0);

    // All five area tabs fit on the screen, side by side, with 44 px targets.
    const nav = await page.getByTestId('guardian-domain-nav').boundingBox();
    for (const id of ['all', 'overview', 'heart', 'time', 'safety']) {
      const box = await page.getByTestId(`guardian-area-${id}`).boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual((nav?.x ?? 0) + (nav?.width ?? 0) + 1);
    }

    // The area and child are in the address, so a reload lands back on them.
    // On a cold dev server the first tap can land before the page settles: tap until it takes.
    const safety = page.getByTestId('guardian-area-safety');
    await expect(async () => {
      await safety.click();
      await expect(safety).toHaveAttribute('aria-selected', 'true', { timeout: 1000 });
    }).toPass({ timeout: 15_000 });
    await page.getByRole('button', { name: 'Sam' }).click();
    await expect(page).toHaveURL(/area=safety/);
    await expect(page).toHaveURL(/member=sam/);
    await page.reload();
    await expect(page.getByRole('tab', { name: 'Safety & Devices' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('guardian-child-status')).toContainText('Sam');

    // Notices fold to a few rows; the rest are one tap away.
    const notices = page.getByTestId('guardian-notification-locks-card');
    await expect(notices.getByRole('switch')).not.toHaveCount(0);
    const before = await notices.getByRole('switch').count();
    await page.getByTestId('notify-show-all').click();
    expect(await notices.getByRole('switch').count()).toBeGreaterThan(before);

    // Quick actions: a lock offers times, not "+30 min".
    await page.getByTestId('guardian-quick-lock').click();
    await expect(page.getByTestId('guardian-quick-options')).toContainText('30 min');
    await expect(page.getByTestId('guardian-quick-options')).not.toContainText('+30');
    await page.screenshot({ path: info.outputPath('parent.png') });

    const o = await overflow(page);
    expect(o.doc).toBeLessThanOrEqual(o.client + 1);
    expect(unmocked).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('"While locked" fits every screen and every choice is a 44px target', async ({ page }, info) => {
    const { unmocked } = await mockApi(page, 'dad');
    await page.goto('/guardian?area=time&member=sam');
    const card = page.getByTestId('locked-sections-card');
    const chat = page.getByTestId('locked-section-chat');
    await expect(chat).toBeVisible();
    for (const id of ['chat', 'calls', 'devices', 'bible', 'music']) {
      // The whole row is the tap target (the label wraps the checkbox).
      const box = await page.getByTestId(`locked-section-${id}`).locator('xpath=ancestor::label[1]').boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual((page.viewportSize()?.width ?? 0) + 1);
    }
    await chat.scrollIntoViewIfNeeded();
    await card.screenshot({ path: info.outputPath(`while-locked-${info.project.name}.png`) });
    await info.attach(`while locked (${info.project.name})`, { path: info.outputPath(`while-locked-${info.project.name}.png`), contentType: 'image/png' });
    const o = await overflow(page);
    expect(o.doc).toBeLessThanOrEqual(o.client + 1);
    expect(unmocked).toEqual([]);
  });

  test('a mother gets no device policies or devices', async ({ page }) => {
    const { unmocked } = await mockApi(page, 'mom');
    await page.goto('/guardian?area=safety');
    await expect(page.getByTestId('guardian-notification-locks-card')).toBeVisible();
    await expect(page.getByTestId('guardian-device-policy-card')).toHaveCount(0);
    expect(unmocked).toEqual([]);
  });

  test('a child sees their own side', async ({ page }) => {
    const { unmocked } = await mockApi(page, 'sam');
    await page.goto('/guardian');
    await expect(page.getByTestId('talk-request')).toBeVisible();
    await expect(page.getByTestId('guardian-domain-nav')).toHaveCount(0);
    const o = await overflow(page);
    expect(o.doc).toBeLessThanOrEqual(o.client + 1);
    expect(unmocked).toEqual([]);
  });
});
