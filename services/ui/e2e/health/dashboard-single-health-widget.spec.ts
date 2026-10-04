import { test, expect, type Page, type Route } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { seedAuth } from '../fixtures/mediaMocks';

const SHOTS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../.tmp/shots');
fs.mkdirSync(SHOTS_DIR, { recursive: true });

const today = new Date().toISOString().slice(0, 10);

/**
 * The dashboard used to carry two health widgets: `health_hero`, whose registry
 * label was "Today", and `health_activity`, labelled "Health". Both read step data
 * and both drew a ring, so the same numbers appeared twice under two names.
 *
 * These run at Pixel 7 width, because the grid is a bento layout: removing a cell
 * changes the flow for every other cell, which unit tests cannot see.
 */
async function mockDashboard(page: Page) {
  const unhandled: string[] = [];
  page.on('pageerror', (e) => console.log(`[pageerror] ${e.message}`));

  await page.route('**/execute/**', (route: Route) => {
    unhandled.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    return route.fulfill({ json: { status: 'SUCCESS' } });
  });

  await page.route('**/api/**', (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const json = (body: unknown) => route.fulfill({ json: body });

    if (p === '/api/users/me') return route.fallback();
    // The Health widget's data. `last_synced` must come off the real clock: the
    // widget compares it against Date.now() to show freshness.
    if (p === '/api/geo/steps') {
      return json({
        user_id: 'admin',
        daily_steps: { [today]: 7330, '2026-09-30': 9120, '2026-09-29': 6400 },
        today: 7330,
        goal: 10000,
        sources: { phone: 7330 },
        last_synced: Date.now() / 1000,
      });
    }
    if (p === '/api/geo/steps/ranges') {
      return json({
        user_id: 'admin',
        range: url.searchParams.get('range') || 'W',
        label: 'Last 7 days',
        buckets: [],
        total: 7330,
        daily_average: 7620,
        days_recorded: 3,
        goal: 10000,
        baseline: null,
        baseline_min_days: 7,
        thin: true,
        has_gaps: false,
        best: { label: 'Best', steps: 9120 },
      });
    }
    if (p === '/api/logs') return json([]);
    if (p === '/api/entities') return json({ entities: [] });
    if (p === '/api/users/me/theme') return json({ theme: 'dark' });
    if (p.startsWith('/api/geo/')) return json({});
    unhandled.push(`${route.request().method()} ${p}`);
    // An array is the safer default: most shell collections are `.map`ed
    // straight away and an object crashes immediately.
    return route.fulfill({ json: [] });
  });
  return { unhandled };
}

async function open(page: Page) {
  await seedAuth(page);
  const { unhandled } = await mockDashboard(page);
  // The dashboard is the root route (see App.tsx), not /dashboard.
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  // A crash lands in the global error boundary, not a pageerror.
  await page.waitForFunction(
    () => !document.body.textContent?.includes('Encountered a UI Error'),
    undefined,
    { timeout: 20_000 },
  );
  await page.waitForTimeout(1500);
  return unhandled;
}

test.describe('dashboard: one health widget, phone width', () => {
  test('shows the Health widget exactly once and no stray "Today" widget', async ({ page }) => {
    await open(page);

    await expect(page.locator('[data-testid="health-activity-widget"]')).toHaveCount(1);
    // The removed widget's testid must be gone, not merely hidden.
    await expect(page.locator('[data-testid="health-hero-widget"]')).toHaveCount(0);

    // "Today" must not appear as a standalone widget heading any more. It is
    // still used as an inner sub-label inside the Health card, which is fine, so
    // assert on the *heading* specifically rather than the word.
    const widgetHeadings = await page
      .locator('[data-testid="health-activity-widget"] h3, [data-testid="health-hero-widget"] h3')
      .allTextContents();
    expect(widgetHeadings.filter((t) => t?.trim() === 'Today')).toEqual([]);
  });

  test('fits the phone viewport with no horizontal overflow', async ({ page }) => {
    await open(page);
    const m = await page.evaluate(() => ({
      docScrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
    }));
    expect(m.docScrollW).toBeLessThanOrEqual(m.clientW + 1);
    await page.screenshot({ path: path.join(SHOTS_DIR, 'dashboard-one-health-widget.png'), fullPage: false });
  });

  test('the Health card is not squeezed by a neighbouring widget', async ({ page }) => {
    await open(page);
    const card = page.locator('[data-testid="health-activity-widget"]');
    await expect(card).toBeVisible();
    const box = await card.boundingBox();
    expect(box).not.toBeNull();
    // A ~280px column is the phone minimum; anything far narrower means a
    // sibling is overflowing into it.
    expect(box!.width).toBeGreaterThan(240);

    const overflow = await card.evaluate((el) => {
      const e = el as HTMLElement;
      // Name the widest descendant, so a failure says *what* is too wide rather
      // than just that something is.
      const limit = e.clientWidth;
      const culprits = Array.from(e.querySelectorAll<HTMLElement>('*'))
        .filter((n) => n.getBoundingClientRect().right > e.getBoundingClientRect().right + 1)
        .slice(0, 5)
        .map((n) => ({
          tag: n.tagName.toLowerCase(),
          cls: (n.className || '').toString().slice(0, 70),
          testid: n.getAttribute('data-testid'),
          right: Math.round(n.getBoundingClientRect().right),
        }));
      return { scrollW: e.scrollWidth, clientW: e.clientWidth, culprits };
    });
    expect(
      overflow.scrollW,
      `card too wide; widest offenders: ${JSON.stringify(overflow.culprits)}`
    ).toBeLessThanOrEqual(overflow.clientW + 1);
  });

  test('the Health card is tappable at phone size', async ({ page }) => {
    await open(page);
    const card = page.locator('[data-testid="health-activity-widget"]');
    const box = await card.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  });

  test('the Health card renders real data, so nothing failed silently', async ({ page }) => {
    await open(page);
    const card = page.locator('[data-testid="health-activity-widget"]');
    // The mocked daily step total must reach the card. A widget that mounts but
    // renders an empty panel — a "looks broken" dashboard — would pass every
    // structural assertion above, so assert the number itself.
    await expect(card).toContainText('7,330');
    await expect(card).toContainText('10,000');

    // And the freshness line, which is what distinguishes "quiet day" from
    // "the sensor died an hour ago".
    await expect(card.locator('[data-testid="health-sync-status"]')).toBeVisible();
  });
});