import { test, expect as baseExpect, type Page, type TestInfo } from '@playwright/test';
import { mockHome, PROFILES } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockDashboard } from '../fixtures/dashboardMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { INTEGRATIONS_STATUS_PATH, integrationsStatus } from '../fixtures/serverCalls';
import { expectClearOfBottomBar, expectNoHorizontalOverflow, isPhoneShell } from '../fixtures/layoutChecks';
import { quietHotReload, screenFindings, settle, shot, type Allowed } from '../fixtures/pageAudit';

/**
 * The optional home integrations (HomeHub, Home Assistant) on every form
 * factor. With no hub connected, the hub pages leave the nav (sidebar, phone
 * bottom bar and its More sheet), their addresses say how to connect one, the
 * hub widgets leave the dashboard without leaving holes, and the Devices page
 * keeps only the family's part. With HomeHub on, all of it is back. Hermetic:
 * the shared mocks answer every call; this spec only changes the status.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const HUB_PAGES = ['Rooms', 'Climate', 'Cameras', 'Energy', 'Remote', 'Schedules', 'Automations', 'History', 'Lists'];
const HUB_WIDGETS = ['device_control', 'climate', 'energy_insights'];

/** Deliberate designs the audit would otherwise report (as in every-page-dashboard.spec.ts). */
const ALLOWED: Allowed[] = [
  { where: /./, finding: /under 44px: Directions \(/,
    why: 'A link inside the person\'s own markdown note is inline text (the WCAG 2.5.8 inline exception)' },
];

async function open(page: Page, hub: { homehub: boolean; homeAssistant: boolean }, path = '/',
  setup?: (page: Page) => Promise<void>) {
  await quietHotReload(page);
  const home = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockDashboard(page);
  await mockSettingsPages(page);
  // Registered last, so it answers before the shared mocks' "both on".
  await page.route((url) => url.pathname === INTEGRATIONS_STATUS_PATH,
    (route) => route.fulfill({ json: integrationsStatus(hub.homehub, hub.homeAssistant) }));
  if (setup) await setup(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(path);
  return { home, errors };
}

/** The page names the nav offers, read from the sidebar or the phone's bottom bar plus its More sheet. */
async function navPages(page: Page): Promise<string[]> {
  if (await isPhoneShell(page)) {
    const bar = page.locator('nav').filter({ has: page.getByRole('button', { name: 'More pages' }) });
    const pinned = await bar.getByRole('link').allInnerTexts();
    await page.getByRole('button', { name: 'More pages' }).click();
    const sheet = page.getByTestId('more-sheet');
    await expect(sheet).toBeVisible();
    const more = await sheet.getByRole('link').allInnerTexts();
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    return [...pinned, ...more].map((t) => t.trim());
  }
  const sidebar = page.getByTestId('nav-section-everyday').locator('xpath=ancestor::nav[1]');
  return (await sidebar.getByRole('link').evaluateAll((links) => links.map((a) => a.getAttribute('href') ?? '')))
    .map((href) => href);
}

async function audit(page: Page, info: TestInfo, name: string) {
  await settle(page);
  const found = await screenFindings(page, page.locator('main'), name, ALLOWED);
  await expectNoHorizontalOverflow(page);
  await expectClearOfBottomBar(page);
  await shot(page, info, name);
  return found;
}

test.describe('home integrations on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('no hub: the hub pages and widgets are gone, with no holes left behind', async ({ page }, info) => {
    const run = await open(page, { homehub: false, homeAssistant: false });
    const main = page.locator('main');
    await expect(main.getByText('Unload the dishwasher')).toBeVisible();
    for (const key of HUB_WIDGETS) await expect(page.locator(`[data-widget="${key}"]`)).toHaveCount(0);
    // The dashboard spec turns on 18 widgets; three of them read the hub.
    await expect(page.locator('[data-widget]')).toHaveCount(18 - HUB_WIDGETS.length);
    // Every widget left on the board is drawn (no empty cell standing in for a missing one).
    for (const box of await page.locator('[data-widget]').all()) {
      const b = await box.boundingBox();
      expect(b && b.width > 40 && b.height > 40).toBe(true);
    }
    const found = await audit(page, info, 'integrations-off-dashboard');

    const pages = await navPages(page);
    if (await isPhoneShell(page)) {
      for (const name of HUB_PAGES) expect(pages).not.toContain(name);
      expect(pages).toContain('Devices');
    } else {
      for (const name of HUB_PAGES) expect(pages).not.toContain(`/${name.toLowerCase()}`);
      expect(pages).toContain('/devices');
      await expect(page.getByTestId('nav-section-home')).toHaveCount(0);
    }
    expect(found).toEqual([]);
    expect(run.errors).toEqual([]);
  });

  test('no hub: a hub page visited directly says how to connect one', async ({ page }, info) => {
    const run = await open(page, { homehub: false, homeAssistant: false }, '/rooms');
    const gate = page.getByTestId('hub-required');
    await expect(gate).toBeVisible();
    await expect(gate.getByText('Connect a home hub in Settings')).toBeVisible();
    await expect(gate.getByRole('link', { name: 'Open Accounts & Integrations' })).toHaveAttribute('href', '/identity');
    expect(await audit(page, info, 'integrations-off-rooms')).toEqual([]);
    expect(run.errors).toEqual([]);
  });

  test('no hub: Devices keeps the family part and drops the hub tabs', async ({ page }, info) => {
    const run = await open(page, { homehub: false, homeAssistant: false }, '/devices');
    await expect(page.getByText('Family devices')).toBeVisible();
    await expect(page.getByTestId('devices-sections')).toHaveCount(0);
    await expect(page.getByTestId('hardware')).toHaveCount(0);
    expect(await audit(page, info, 'integrations-off-devices')).toEqual([]);
    expect(run.errors).toEqual([]);
  });

  test('Home Assistant alone: pages any hub serves come back, HomeHub pages stay away', async ({ page }) => {
    await open(page, { homehub: false, homeAssistant: true });
    await expect(page.locator('main').getByText('Unload the dishwasher')).toBeVisible();
    await expect(page.locator('[data-widget="device_control"]')).toHaveCount(1);
    const pages = await navPages(page);
    const has = (name: string) => pages.includes(name) || pages.includes(`/${name.toLowerCase()}`);
    for (const name of ['Remote', 'History', 'Lists']) expect(has(name), name).toBe(true);
    for (const name of ['Rooms', 'Climate', 'Cameras', 'Energy', 'Schedules', 'Automations']) expect(has(name), name).toBe(false);
  });

  test('HomeHub on: every hub page and widget is offered', async ({ page }, info) => {
    const run = await open(page, { homehub: true, homeAssistant: false });
    await expect(page.locator('main [data-widget="device_control"]').getByText('Garage door')).toBeVisible();
    await expect(page.locator('[data-widget]')).toHaveCount(18);
    const pages = await navPages(page);
    const has = (name: string) => pages.includes(name) || pages.includes(`/${name.toLowerCase()}`);
    for (const name of HUB_PAGES) expect(has(name), name).toBe(true);
    await shot(page, info, 'integrations-on-dashboard');
    expect(run.errors).toEqual([]);
  });

  test('Accounts & Integrations: the HomeHub card, and Home Assistant saying it is off', async ({ page }, info) => {
    const tests: unknown[] = [];
    await open(page, { homehub: true, homeAssistant: false }, '/identity', async (p) => {
      await p.route('**/api/spotify/status', (route) => route.fulfill({ json: { configured: false, missing: [], linked: false } }));
      await p.route('**/api/users/me/voice', (route) => route.fulfill({
        json: { enrolled: false, samples: 0, min_samples: 3, max_samples: 10, consistency: null, model: 'test', updated_at: null } }));
      await p.route('**/api/user-panel/devices', (route) => route.fulfill({ json: [] }));
      await p.route('**/api/users/me/keys', (route) => route.fulfill({ json: [] }));
      await p.route('**/api/users/me', (route) => route.fulfill({ json: PROFILES.admin }));
      await p.route('**/api/settings', (route) => route.fulfill({
        json: [{ key: 'homehub_url', value: 'http://hub.example.test:8123' }] }));
      await p.route('**/api/auth/test-connection', (route) => {
        tests.push(route.request().postDataJSON());
        return route.fulfill({ json: { status: 'SUCCESS', message: 'Connected to HomeHub (12 devices loaded)' } });
      });
    });
    const tile = page.getByTestId('homehub-tile');
    await expect(tile).toBeVisible();
    await expect(page.getByTestId('integration-status-HomeHub')).toHaveText('Connected: hub.example.test:8123');
    await expect(page.getByTestId('integration-status-Home Assistant')).toHaveText('INTEGRATION OFF');
    await tile.getByRole('button', { name: 'Manage HomeHub' }).click();
    const dialog = page.getByTestId('homehub-dialog');
    await expect(dialog.getByLabel('HomeHub address')).toHaveValue('http://hub.example.test:8123');
    await dialog.getByRole('button', { name: 'Test' }).click();
    await expect(page.getByTestId('homehub-test-result')).toContainText('Connected to HomeHub');
    expect(tests).toEqual([{ service: 'HomeHub', config: { homehub_url: 'http://hub.example.test:8123' } }]);
    await shot(page, info, 'integrations-homehub-dialog');
  });

  test('no hub: Admin leaves out the hub tabs and parts, and says so once', async ({ page }, info) => {
    const run = await open(page, { homehub: false, homeAssistant: false }, '/admin/users');
    const main = page.locator('main');
    const tabs = page.getByRole('tablist', { name: 'Admin sections' });
    await expect(tabs.getByRole('tab', { name: 'Users & Devices' })).toBeVisible();
    for (const name of ['Device Groups', 'Telemetry']) await expect(tabs.getByRole('tab', { name })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Sync Device Discovery' })).toHaveCount(0);
    // Device assignments, Entity Protection and Home alerts: one notice, not three.
    await expect(main.getByTestId('hub-not-connected')).toHaveCount(1);
    await expect(main.getByText('Device Assignments', { exact: true })).toHaveCount(0);
    expect(await audit(page, info, 'integrations-off-admin-users')).toEqual([]);

    // An old link to a hub tab says what it needs instead of failing.
    await page.goto('/admin/groups');
    await expect(main.getByTestId('hub-not-connected')).toContainText('Device Groups');
    await expect(main.getByRole('button', { name: /Media Groups/ })).toHaveCount(0);
    expect(await audit(page, info, 'integrations-off-admin-groups')).toEqual([]);

    // Intercom keeps sessions and its settings; broadcast and announce send to hub speakers.
    await page.goto('/admin/intercom');
    await expect(main.getByRole('button', { name: 'Sessions', exact: true })).toBeVisible();
    await expect(main.getByRole('button', { name: 'Config', exact: true })).toBeVisible();
    for (const name of ['Broadcast', 'Announce']) await expect(main.getByRole('button', { name, exact: true })).toHaveCount(0);
    expect(await audit(page, info, 'integrations-off-admin-intercom')).toEqual([]);
    expect(run.errors).toEqual([]);
  });

  test('a hub connected: Admin offers every hub tab and part', async ({ page }) => {
    const run = await open(page, { homehub: false, homeAssistant: true }, '/admin/users');
    const tabs = page.getByRole('tablist', { name: 'Admin sections' });
    for (const name of ['Device Groups', 'Telemetry']) await expect(tabs.getByRole('tab', { name })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sync Device Discovery' })).toBeVisible();
    await expect(page.locator('main').getByText('Device Assignments', { exact: true })).toBeVisible();
    await expect(page.locator('main').getByTestId('hub-not-connected')).toHaveCount(0);
    expect(run.errors).toEqual([]);
  });
});
