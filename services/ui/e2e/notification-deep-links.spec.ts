import { test, expect as baseExpect, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks, type Role } from './fixtures/homeMocks';
import { mockHealth } from './fixtures/healthMocks';
import { mockBible } from './fixtures/bibleMocks';
import { mockFamilyPages } from './fixtures/familyPagesMocks';
import { settle, shot, quietHotReload } from './fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Tapping a notice opens the page it is about, at the item (docs/PUSH.md,
 * "Deep links"): the bell follows a notice's `data.link`, the page scrolls to
 * the item and highlights it, an item that is gone leaves the page with a
 * clear word, and a page the person may not open says so instead of jumping
 * home. A phone notification lands on the same address. Hermetic; every form
 * factor of playwright.config.ts runs it.
 */

interface Run { mocks: HomeMocks; errors: string[] }

function unlockRequest(id: number) {
  const now = Date.now();
  return {
    id, username: 'jordan', device: 'PC', lock_reason: 'bedtime', note: 'Science project', minutes_asked: 30,
    status: 'pending', minutes_granted: null, until: null, decided_by: null,
    created_at: new Date(now - 60_000).toISOString(), expires_at: new Date(now + 9 * 60_000).toISOString(), decided_at: null,
  };
}

const UNLOCK_LINK = {
  route: 'member', path: '/family/member/jordan', query: {}, hash: '', focus: 'unlock-21', guard: 'parent',
  href: '/family/member/jordan?focus=unlock-21',
};

async function open(page: Page, role: Role = 'admin', notices: unknown[] = []): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, role);
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const run: Run = { mocks, errors: [] };
  // Registered last, so these answer before the shared mocks.
  await page.route('**/api/guard/unlock-requests*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({ json: { requests: [unlockRequest(21)] } });
  });
  await page.route('**/api/telemetry/notifications*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({ json: { notifications: notices } });
  });
  page.on('pageerror', (e) => run.errors.push(e.message));
  return run;
}

async function inView(page: Page, selector: string): Promise<boolean> {
  return page.locator(selector).evaluate((el) => {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
  });
}

async function done(page: Page, info: TestInfo, run: Run, name: string) {
  await settle(page);
  await shot(page, info, name);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
}

test.describe('notification deep links', () => {
  test.describe.configure({ timeout: 120_000 });

  test('a child\'s unlock request opens at the request, highlighted', async ({ page }, info) => {
    const run = await open(page);
    await page.goto(UNLOCK_LINK.href);
    const row = page.locator('[data-focus-id="unlock-21"]');
    await expect(row).toBeVisible();
    await expect(row).toHaveClass(/deep-link-highlight/);
    await expect.poll(() => inView(page, '[data-focus-id="unlock-21"]')).toBe(true);
    // The parameter is dropped, so Back or a reload does not repeat it.
    await expect.poll(() => new URL(page.url()).searchParams.has('focus')).toBe(false);
    await expect(page).toHaveURL(/\/family\/member\/jordan$/);
    // Its buttons still answer it.
    await expect(row.getByRole('button', { name: /Approve/ })).toBeVisible();
    await done(page, info, run, 'unlock request highlighted');
  });

  test('an item that is gone leaves the page and says so', async ({ page }, info) => {
    const run = await open(page);
    await page.goto('/family/member/jordan?focus=unlock-99');
    await expect(page.getByTestId('unlock-requests')).toBeVisible();
    await expect(page.getByText('That unlock request is no longer here.', { exact: false })).toBeVisible({ timeout: 25_000 });
    await expect(page).toHaveURL(/\/family\/member\/jordan$/);
    await done(page, info, run, 'unlock request gone');
  });

  test('the bell follows a notice\'s link to the item', async ({ page }, info) => {
    const run = await open(page, 'admin', [{
      id: 'n1', kind: 'family_safety', title: 'Unlock Jordan\'s PC?', body: 'Jordan asks to unlock their PC for 30 minutes.',
      created_at: new Date().toISOString(), read: false,
      // A wrong url beside the link: the link wins.
      data: { link: UNLOCK_LINK, url: '/' },
    }]);
    await page.goto('/');
    await page.getByRole('button', { name: /^Notifications/ }).first().click();
    await page.getByTestId('notification-open').first().click();
    await expect(page).toHaveURL(/\/family\/member\/jordan/);
    await expect(page.locator('[data-focus-id="unlock-21"]')).toHaveClass(/deep-link-highlight/);
    await done(page, info, run, 'bell opens the request');
  });

  test('a page the person may not open says so, never a silent jump home', async ({ page }, info) => {
    const run = await open(page, 'member');
    await page.goto('/admin/ops?mission=4');
    const refused = page.getByTestId('access-refused');
    await expect(refused).toBeVisible();
    await expect(refused).toContainText('You can’t open this here');
    await expect(refused.getByRole('link', { name: 'Go to the dashboard' })).toBeVisible();
    // Nothing of the admin page is loaded behind it.
    await expect(page.getByRole('tablist', { name: 'Admin sections' })).toHaveCount(0);
    await done(page, info, run, 'admin page refused');
  });
});
