import { test, expect, type Page, type Route } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { seedAuth } from '../fixtures/mediaMocks';

const SHOTS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../.tmp/shots');
fs.mkdirSync(SHOTS_DIR, { recursive: true });

const HOURS = [
  { hour: 6, label: '06:00', steps: 40 },
  { hour: 7, label: '07:00', steps: 320 },
  { hour: 8, label: '08:00', steps: 940 },
  { hour: 12, label: '12:00', steps: 180 },
  { hour: 13, label: '13:00', steps: 610 },
  { hour: 17, label: '17:00', steps: 240 },
];

const today = new Date().toISOString().slice(0, 10);

function ranges(range: string) {
  const base = {
    user_id: 'admin',
    range,
    label: range === 'D' ? 'Today' : 'Last 7 days',
    total: 2330,
    daily_average: 2330,
    days_recorded: range === 'D' ? 1 : 7,
    goal: 10000,
    baseline: null,
    baseline_min_days: 7,
    thin: true,
    has_gaps: false,
    best: { label: 'Today', steps: 2330 },
  };
  if (range === 'D') {
    return {
      ...base,
      buckets: [{ label: 'Today', start: today, end: today, steps: 2330, days_missing: 0, days_recorded: 1, complete: false }],
      hourly: HOURS,
      peak: HOURS[2],
    };
  }
  return {
    ...base,
    buckets: Array.from({ length: 7 }, (_, i) => ({
      label: `D${i + 1}`,
      start: today,
      end: today,
      steps: 1800 + i * 90,
      days_missing: 0,
      days_recorded: 1,
      complete: true,
    })),
  };
}

async function mockHealth(page: Page, opts: { hourly?: boolean } = {}) {
  const unhandled: string[] = [];
  page.on('pageerror', (e) => console.log(`[pageerror] ${e.message}\n${(e.stack ?? '').split('\n').slice(0, 5).join('\n')}`));
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const json = (body: unknown) => route.fulfill({ json: body });
    if (p === '/api/users/me') return route.fallback();
    if (p === '/api/geo/steps/ranges') {
      const payload = ranges(url.searchParams.get('range') || 'W');
      // Older builds and phones that have not reported hours: the keys are
      // absent, which is what the UI has to render honestly.
      if (opts.hourly === false && 'hourly' in payload) {
        const { hourly: _h, peak: _p, ...rest } = payload;
        void _h;
        void _p;
        return json(rest);
      }
      return json(payload);
    }
    if (p === '/api/geo/steps') {
      return json({ user_id: 'admin', daily_steps: { [today]: 2330 }, today: 2330, goal: 10000, sources: { phone: 2330 }, last_synced: Date.now() / 1000 });
    }
    if (p === '/api/geo/metrics/catalog') return json({ available: [], unavailable: {} });
    if (p === '/api/geo/stars') return json({ user_id: 'admin', stars: 4, grants: [] });
    if (p === '/api/geo/achievements') return json({ user_id: 'admin', days: 30, achievements: [] });
    if (p === '/api/geo/events') return json({ user_id: 'admin', empty: true, total_events: 0, window_days: 30, groups: [] });
    if (p === '/api/geo/activity/summary') return json({ user_id: 'admin', days: 7, users: [] });
    if (p === '/api/geo/activity/feed') return json({ user_id: 'admin', events: [] });
    if (p === '/api/geo/trends/activity') return json({ user_id: 'admin', days: 7 });
    if (p === '/api/geo/workouts') return json({ workouts: [], total: 0 });
    if (p === '/api/geo/workouts/active') return json({ active: null });
    if (p === '/api/geo/trips') return json({ trips: [], total_trips: 0 });
    if (p === '/api/geo/vehicles') return json({ vehicles: [] });
    if (p === '/api/geo/people') return json({ features: [] });
    if (p === '/api/logs') return json([]);
    if (p === '/api/users/me/theme') return json({ theme: 'dark' });
    if (p.startsWith('/api/geo/')) return json({});
    unhandled.push(`${route.request().method()} ${url.pathname}`);
    // An array is the safer default here: most of the shell's collections are
    // `.map`ed straight away, and an object is an instant crash.
    return route.fulfill({ json: [] });
  });
  return { unhandled };
}

/** The Health page keeps a 30 s step-sync timer and a location heartbeat, so the
 * tree can churn while it is still filling in. Clicking into a remount is a
 * harness flake, not a product bug, so wait for the selector to hold still. */
async function settle(page: Page) {
  await page.waitForFunction(
    () => document.querySelector('[data-testid="range-D"]') !== null,
    undefined,
    { timeout: 30_000 },
  );
  await page.waitForTimeout(1500);
  await page.waitForFunction(
    () => document.querySelector('[data-testid="steps-card"]') !== null,
    undefined,
    { timeout: 30_000 },
  );
}

interface Overflow {
  docScrollW: number;
  clientW: number;
  cardScrollW: number;
  cardClientW: number;
  chartScrollW: number;
  chartClientW: number;
  bars: number;
  minBarW: number;
  controls: Array<{ label: string; h: number }>;
  wide: string[];
}

async function measure(page: Page, testId: string): Promise<Overflow> {
  return page.evaluate((id) => {
    const el = document.querySelector(`[data-testid="${id}"]`);
    if (!el) throw new Error(`${id} never rendered`);
    const card = (el.closest('section') || el) as HTMLElement;
    const bars = Array.from(el.querySelectorAll<HTMLElement>('[data-testid^="hour-bar-"]'));
    // The widest thing inside the card is the thing that has to change.
    const cardRight = card.getBoundingClientRect().right;
    const wide = Array.from(card.querySelectorAll<HTMLElement>('*'))
      .map((n) => ({ n, over: Math.round(n.getBoundingClientRect().right - cardRight) }))
      .filter((x) => x.over > 1)
      .sort((a, b) => b.over - a.over)
      .slice(0, 5)
      .map((x) => `${x.n.tagName}.${x.n.className.toString().slice(0, 70)}=+${x.over}`);
    const controls = Array.from(
      card.querySelectorAll<HTMLElement>('button, select, input[type="checkbox"]'),
    ).map((n) => ({
      label: (n.getAttribute('aria-label') || n.textContent || n.tagName).trim().slice(0, 24),
      h: Math.round(n.getBoundingClientRect().height),
    }));
    return {
      docScrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
      cardScrollW: card.scrollWidth,
      cardClientW: card.clientWidth,
      chartScrollW: (el as HTMLElement).scrollWidth,
      chartClientW: (el as HTMLElement).clientWidth,
      bars: bars.length,
      minBarW: bars.length ? Math.min(...bars.map((b) => Math.round(b.getBoundingClientRect().width))) : 0,
      controls,
      wide,
    };
  }, testId);
}

test.describe('hourly step chart on a phone', () => {
  test('fits a Pixel 7 without sideways scroll and keeps 44px controls', async ({ page }) => {
    await seedAuth(page);
    await mockHealth(page);
    page.on('pageerror', (e) => console.log('[pageerror]', e.message));
    await page.goto('/fitness');

    await settle(page);
    await page.getByTestId('range-D').click();
    await expect(page.getByTestId('step-hour-chart')).toBeVisible();

    const m = await measure(page, 'step-hour-chart');
    console.log('METRICS', JSON.stringify(m));
    expect(m.docScrollW).toBeLessThanOrEqual(m.clientW + 1);
    // The chart itself must not need sideways scrolling; the card's own
    // scrollWidth includes the ring's SVG box, which is not user-visible.
    expect(m.chartScrollW).toBeLessThanOrEqual(m.chartClientW + 1);
    // 24 hours, all of them drawn.
    expect(m.bars).toBe(24);
    // Every hour still gets a bar you can see.
    expect(m.minBarW).toBeGreaterThanOrEqual(2);

    await page.getByTestId('steps-card').screenshot({ path: path.join(SHOTS_DIR, 'health-day-hours.png') });
  });

  test('the busy hours are emphasised and the empty hours are not silent', async ({ page }) => {
    await seedAuth(page);
    await mockHealth(page);
    await page.goto('/fitness');
    await settle(page);
    await page.getByTestId('range-D').click();
    await expect(page.getByTestId('step-hour-chart')).toBeVisible();

    await expect(page.getByTestId('hour-bar-8')).toHaveAttribute('data-state', 'recorded');
    await expect(page.getByTestId('hour-chart-busiest')).toContainText('Busiest 8am');
    await page.getByTestId('step-hour-chart').screenshot({ path: path.join(SHOTS_DIR, 'hour-chart.png') });
  });

  test('a phone with no hourly data is told so', async ({ page }) => {
    await seedAuth(page);
    await mockHealth(page, { hourly: false });
    await page.goto('/fitness');
    await settle(page);
    await page.getByTestId('range-D').click();

    await expect(page.getByTestId('hour-chart-missing')).toBeVisible();
    await expect(page.getByTestId('step-hour-chart')).toHaveCount(0);
    await page.getByTestId('steps-card').screenshot({ path: path.join(SHOTS_DIR, 'health-day-no-hours.png') });
  });
});

test.describe('admin bonus stars on a phone', () => {
  test('needs a recipient and the grant form is thumb-sized', async ({ page }) => {
    await seedAuth(page);
    await mockHealth(page);
    await page.route('**/api/users', async (route) =>
      route.fulfill({
        json: [
          { id: 1, username: 'jeremiah', full_name: 'Jeremiah', is_admin: true, is_system_default: false },
          { id: 2, username: 'michele', full_name: 'Michele', is_admin: false, is_system_default: false },
        ],
      }),
    );
    await page.goto('/admin/users');

    await expect(page.getByTestId('admin-stars')).toBeVisible();
    // The picker fills in from /api/users; selecting before that lands would be
    // selecting nothing.
    await expect(page.getByLabel('Star recipient').locator('option', { hasText: '@michele' })).toHaveCount(1);
    await page.getByLabel('Star recipient').selectOption('michele');
    await expect(page.getByTestId('star-balance')).toBeVisible();

    const m = await measure(page, 'star-balance');
    console.log('ADMIN METRICS', JSON.stringify(m));
    expect(m.docScrollW).toBeLessThanOrEqual(m.clientW + 1);
    expect(m.cardScrollW).toBeLessThanOrEqual(m.cardClientW + 1);
    // The form's own controls, not the panel chrome.
    for (const c of m.controls.filter((c) => /grant|star|mirror/i.test(c.label))) {
      expect(c.h, `${c.label} must be a real touch target`).toBeGreaterThanOrEqual(40);
    }
    await page.getByTestId('admin-stars').screenshot({ path: path.join(SHOTS_DIR, 'admin-stars.png') });
  });
});