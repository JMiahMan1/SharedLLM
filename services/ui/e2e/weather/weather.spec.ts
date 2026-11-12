/**
 * Weather across form factors (docs/WEATHER.md), hermetic: every API call is
 * mocked, nothing reaches a server.
 *
 * Each viewport (a small phone, a Pixel, an iPad both ways, a desktop) is
 * checked on the dashboard (the widget at every size, and the Climate
 * widget's outdoor badge), the calendar (every view), Settings and Admin,
 * and again with the weather service broken (5xx, not set up, timing out,
 * no forecast days). The rules are the same everywhere: no horizontal page
 * scroll, no widget card wider than its cell, no "Widget failed", no page
 * error, and no other part of the page affected when weather is away.
 *
 * Runs once (desktop project or a local config); it sweeps its own viewports.
 */
import { expect, test, type Page, type Route } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';
import { shotPath } from '../fixtures/climateMocks';

type Scenario = 'ok' | 'error' | 'unset' | 'slow' | 'empty';
type Size = 'small' | 'medium' | 'wide' | 'tall';

const VIEWPORTS = [
  { name: 'phone-375', width: 375, height: 667, mobile: true },
  { name: 'pixel-412', width: 412, height: 915, mobile: true },
  { name: 'ipad-768', width: 768, height: 1024, mobile: true },
  { name: 'ipad-1180', width: 1180, height: 820, mobile: true },
  { name: 'desktop-1440', width: 1440, height: 900, mobile: false },
];

const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

function forecast() {
  const now = Date.now() / 1000;
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
      const d = new Date();
      d.setDate(d.getDate() + i);
      return { date: ymd(d), ts: 0, high: 78 - i * 3, low: 57 - i * 4, precip: i === 2 ? 0.7 : 0,
        precip_prob: i === 2 ? 82 : 5, snow: 0, gust: 20, code: 0, label: labels[i], icon: icons[i],
        sunrise: null, sunset: null };
    }),
    fetched_at: now,
    personal: false,
    heads_up: [
      { id: 'hay', kind: 'rain', title: 'Cover the hay', body: 'Barn: Rain likely from 3 PM (85% chance, about 0.40 in). Tarps are in the barn.', starts_at: now },
      { id: 'pipes', kind: 'freeze', title: 'Drip the taps', body: 'Down to 29°F around 5 AM tomorrow.', starts_at: now + 3600 },
    ],
  };
}

const ENERGY = {
  units: '°F', days: [], model: { baseline_kwh: 10, kwh_per_degree_day: 0.8, r2: 0.9, days: 12 },
  insights: ['Yesterday: 34.0 kWh tracked, heat ran 6.0 h, outside averaged 35°F.',
    'Tracked energy: about 0.80 kWh per degree-day of heating or cooling, on top of 10.0 kWh a day (12 days, r² 0.9).'],
  projection: Array.from({ length: 7 }, (_, i) => ({ date: ymd(new Date(Date.now() + i * 86400000)), kwh: 20 + i })),
};

const WIDGETS = ['weather', 'climate', 'energy_insights', 'ambient_timer', 'quick_notes', 'active_media', 'chores_progress',
  'upcoming_events', 'quick_assistant', 'device_control', 'workspaces', 'health_activity', 'health_today', 'presence',
  'bible_daily', 'recommendations'];

async function mockAll(page: Page, scenario: Scenario, size: Size = 'medium') {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));

  const catchAll = async (route: Route) => {
    const req = route.request();
    if (req.method() !== 'GET') return route.fulfill({ json: { status: 'SUCCESS', message: 'e2e' } });
    const p = new URL(req.url()).pathname;
    if (/\/api\/(health|info)\b/.test(p)) return route.fulfill({ json: { status: 'ok', version: 'e2e', git_sha: 'e2e' } });
    if (/\/api\/(config|calendar\/settings)\b/.test(p)) return route.fulfill({ json: { status: 'SUCCESS', settings: {}, config: {} } });
    if (/\/api\/telemetry\//.test(p)) return route.fulfill({ json: { notifications: [], jobs: [] } });
    return route.fulfill({ json: [] });
  };
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/') || u.pathname === '/health', catchAll);

  await page.route('**/api/widgets/settings', (route) => route.fulfill({ json: {
    widgets: WIDGETS.map((key, i) => ({
      widget_key: key, visibility: key === 'weather' || key === 'climate' ? 'visible' : 'hidden', order_index: i,
      size: key === 'weather' ? size : 'medium', is_pinned: false, sort_mode: null, pinned_devices: [],
      config: key === 'climate' ? { devices: ['climate.hall'] } : {}, updated_at: 0,
    })),
    quick_assistant_enabled: false,
  } }));
  await page.route('**/execute/entity/search', (route) => route.fulfill({ json: { status: 'ok', result: [{
    entity_id: 'climate.hall', friendly_name: 'Hall', state: 'heat', domain: 'climate', last_updated: new Date().toISOString(),
    attributes: { hvac_mode: 'heat', hvac_action: 'heating', current_temperature: 68, temperature: 70, hvac_modes: ['off', 'heat'],
      min_temp: 50, max_temp: 90 },
  }] } }));
  await page.route('**/api/communication/calendar/events**', (route) => {
    const at = new Date();
    at.setHours(15, 0, 0, 0);
    return route.fulfill({ json: { status: 'SUCCESS', detail: { integrations: [] }, events: [
      { id: 'e1', summary: 'Feed store run', start_time: at.toISOString(), end_time: new Date(at.getTime() + 3600e3).toISOString(), calendar: 'family' },
    ] } });
  });

  // Weather itself, by scenario.
  await page.route('**/api/weather', (route) => {
    if (scenario === 'ok') return route.fulfill({ json: forecast() });
    if (scenario === 'empty') return route.fulfill({ json: { ...forecast(), hourly: [], daily: [], heads_up: [] } });
    if (scenario === 'unset') return route.fulfill({ status: 404, json: { detail: 'No home location' } });
    if (scenario === 'slow') return new Promise<void>(() => { /* never answers */ });
    return route.fulfill({ status: 502, json: { detail: 'The weather service did not answer' } });
  });
  await page.route('**/api/weather/energy**', (route) =>
    scenario === 'ok' ? route.fulfill({ json: ENERGY }) : route.fulfill({ status: 502, json: {} }));
  await page.route('**/api/weather/integrations', (route) => scenario === 'ok'
    ? route.fulfill({ json: { events: ['forecast.updated', 'condition.changed', 'alert.started', 'day.completed'],
      services: ['execution', 'geo', 'health', 'identity', 'telemetry'], metric_modes: [], metric_aggs: [], kinds: [],
      stream: 'weather:events', metrics: [{ name: 'eggs', label: 'Eggs', unit: 'eggs', agg: 'sum', source: 'pushed' }] } })
    : route.fulfill({ status: 502, json: {} }));
  await page.route('**/api/weather/correlations**', (route) => scenario === 'ok'
    ? route.fulfill({ json: { units: '°F', today: '', insights: ENERGY.insights, metrics: [{ name: 'energy_kwh', label: 'Tracked energy', days: 12 }] } })
    : route.fulfill({ status: 502, json: {} }));
  await page.route('**/api/users/me/weather', (route) => scenario === 'ok'
    ? route.fulfill({ json: { status: 'SUCCESS', location: null, units: null } })
    : route.fulfill({ status: 502, json: {} }));
  await page.route('**/api/settings', (route) => route.fulfill({ json: [
    { key: 'weather_reminders', value: JSON.stringify([
      { id: 'hay', kind: 'rain', title: 'Cover the hay', note: 'Tarps are in the barn.', people: ['alex'],
        location: { name: 'Barn', latitude: 36.1, longitude: -86.7 } },
      { id: 'barn', kind: 'rain', title: 'Close the barn door', notify: false,
        actions: [{ type: 'ha_service', service: 'cover.close_cover', entity_id: 'cover.barn_door' }] },
    ]) },
    { key: 'weather_units', value: 'imperial' }, { key: 'weather_location', value: '' },
    { key: 'weather_metrics', value: JSON.stringify([{ name: 'solar', label: 'Solar', entity_id: 'sensor.solar_power', mode: 'power' }]) },
    { key: 'weather_hooks', value: JSON.stringify([{ id: 'h', events: ['alert.started'], service: 'geo', path: '/api/hooks/weather' }]) },
  ] }));
  await seedAuth(page);
  return errors;
}

/** Page-level layout facts that must hold at every width. */
async function layout(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const cards = Array.from(document.querySelectorAll<HTMLElement>(
      '[data-testid="weather-widget"], [data-testid="weather-settings"], [data-testid="weather-reminders"], [data-testid="weather-integrations"]'));
    return {
      pageOverflow: doc.scrollWidth - doc.clientWidth,
      cardOverflow: cards.map((c) => ({ id: c.dataset.testid, over: c.scrollWidth - c.clientWidth }))
        .filter((c) => c.over > 1),
      widgetFailed: document.body.innerText.includes('Widget failed'),
    };
  });
}

test.describe('weather across form factors', () => {
  test.beforeEach(({ browserName }, testInfo) => {
    void browserName;
    // One run is enough: this spec sweeps its own viewports.
    test.skip(!!testInfo.project.name && testInfo.project.name !== 'hermetic-desktop', 'runs in one project');
  });

  for (const vp of VIEWPORTS) {
    test.describe(vp.name, () => {
      test.use({ viewport: { width: vp.width, height: vp.height }, isMobile: vp.mobile, hasTouch: vp.mobile });

      for (const size of ['small', 'medium', 'wide', 'tall'] as Size[]) {
        test(`dashboard widget ${size}`, async ({ page }) => {
          const errors = await mockAll(page, 'ok', size);
          await page.goto('/');
          const widget = page.getByTestId('weather-widget');
          await expect(widget).toBeVisible();
          await expect(page.getByTestId('weather-now')).toHaveText('61°F');
          if (size !== 'small') {
            await expect(page.getByTestId('weather-heads-up')).toContainText('Cover the hay');
            const strip = page.getByRole('list', { name: 'Next 12 hours' });
            await expect(strip).toBeVisible();
            expect(await strip.evaluate((el) => getComputedStyle(el).touchAction)).toContain('pan-x');
          }
          if (size === 'wide' || size === 'tall') await expect(page.getByRole('list', { name: 'This week' })).toBeVisible();
          if (size === 'tall') await expect(page.getByTestId('weather-energy')).toContainText('kWh');
          await expect(page.getByTestId('climate-outdoor')).toHaveText('Out 61°');
          const facts = await layout(page);
          expect(facts.pageOverflow, 'page scrolls sideways').toBeLessThanOrEqual(1);
          expect(facts.cardOverflow).toEqual([]);
          expect(facts.widgetFailed).toBe(false);
          expect(errors).toEqual([]);
          await widget.screenshot({ path: shotPath(`weather-${vp.name}-widget-${size}`) });
        });
      }

      for (const scenario of ['error', 'unset', 'slow', 'empty'] as Scenario[]) {
        test(`dashboard and calendar stay intact when weather is ${scenario}`, async ({ page }) => {
          const errors = await mockAll(page, scenario);
          await page.goto('/');
          // The Climate widget is unaffected: no badge, controls still there.
          await expect(page.getByText('Hall').first()).toBeVisible();
          // No forecast, no badge ("empty" still has a current reading, so it keeps one).
          if (scenario !== 'empty') await expect(page.getByTestId('climate-outdoor')).toHaveCount(0);
          if (scenario === 'error' || scenario === 'unset') {
            await expect(page.getByTestId('weather-widget').or(page.getByRole('status').filter({ hasText: /forecast/ })).first())
              .toBeVisible({ timeout: 20_000 });
          }
          let facts = await layout(page);
          expect(facts.pageOverflow).toBeLessThanOrEqual(1);
          expect(facts.widgetFailed).toBe(false);
          await page.screenshot({ path: shotPath(`weather-${vp.name}-dashboard-${scenario}`) });

          await page.goto('/calendar');
          await expect(page.getByText('Feed store run').first()).toBeVisible();
          await expect(page.getByTestId('day-weather')).toHaveCount(0);
          facts = await layout(page);
          expect(facts.pageOverflow).toBeLessThanOrEqual(1);
          expect(errors).toEqual([]);
        });
      }

      test('calendar shows each day\'s weather in every view', async ({ page }) => {
        const errors = await mockAll(page, 'ok');
        await page.goto('/calendar');
        await expect(page.getByText('Feed store run').first()).toBeVisible();
        for (const view of ['Agenda', 'Week', 'Month', 'Day']) {
          await page.getByRole('button', { name: view, exact: true }).first().click();
          await expect(page.getByTestId('day-weather').first()).toBeVisible();
          const facts = await layout(page);
          expect(facts.pageOverflow, `${view} scrolls sideways`).toBeLessThanOrEqual(1);
          // Nothing the weather adds spills out of the cell it sits in.
          const spill = await page.evaluate(() => Array.from(document.querySelectorAll<HTMLElement>('[data-testid="day-weather"]'))
            .filter((el) => {
              const b = el.getBoundingClientRect();
              const host = el.closest('button, [class*="rounded"]') as HTMLElement | null;
              if (!host || !b.width) return false;
              const h = host.getBoundingClientRect();
              return b.right > h.right + 1 || b.left < h.left - 1;
            }).length);
          expect(spill, `${view}: weather spills out of its day`).toBe(0);
          await page.screenshot({ path: shotPath(`weather-${vp.name}-calendar-${view.toLowerCase()}`) });
        }
        await page.getByRole('button', { name: 'Day', exact: true }).first().click();
        await expect(page.getByTestId('day-weather').first()).toContainText('Clear');
        expect(errors).toEqual([]);
      });

      test('settings and admin panels fit', async ({ page }) => {
        const errors = await mockAll(page, 'ok');
        await page.goto('/settings');
        const mine = page.getByTestId('weather-settings');
        await mine.scrollIntoViewIfNeeded();
        await expect(mine).toBeVisible();
        await mine.getByRole('radio', { name: 'A place I choose' }).click();
        await expect(mine.getByLabel('My weather latitude')).toBeVisible();
        let facts = await layout(page);
        expect(facts.pageOverflow).toBeLessThanOrEqual(1);
        expect(facts.cardOverflow).toEqual([]);
        await mine.screenshot({ path: shotPath(`weather-${vp.name}-settings`) });

        await page.goto('/admin');
        const tab = page.getByRole('tab', { name: /users/i }).or(page.getByRole('button', { name: /users/i })).first();
        if (await tab.isVisible().catch(() => false)) await tab.click();
        const reminders = page.getByTestId('weather-reminders');
        await reminders.scrollIntoViewIfNeeded();
        await expect(reminders).toContainText('Cover the hay');
        await expect(reminders).toContainText('runs cover.close_cover on cover.barn_door');
        const integrations = page.getByTestId('weather-integrations');
        await expect(integrations).toContainText('0.80 kWh per degree-day');
        facts = await layout(page);
        expect(facts.pageOverflow).toBeLessThanOrEqual(1);
        expect(facts.cardOverflow).toEqual([]);
        // Every control a thumb uses is at least 40px tall on touch screens.
        if (vp.mobile) {
          const small = await page.evaluate(() => Array.from(document.querySelectorAll<HTMLElement>(
            '[data-testid="weather-reminders"] button, [data-testid="weather-integrations"] button, [data-testid="weather-settings"] button'))
            // The shared (?) help button is app-wide and out of scope here.
            .filter((b) => b.offsetParent && b.getBoundingClientRect().height < 40 && !b.getAttribute('aria-label')?.startsWith('Help with'))
            .map((b) => (b.getAttribute('aria-label') || b.textContent || '').trim()));
          expect(small, 'tap targets under 40px').toEqual([]);
        }
        await reminders.screenshot({ path: shotPath(`weather-${vp.name}-admin-reminders`) });
        await integrations.screenshot({ path: shotPath(`weather-${vp.name}-admin-integrations`) });
        expect(errors).toEqual([]);
      });
    });
  }
});
