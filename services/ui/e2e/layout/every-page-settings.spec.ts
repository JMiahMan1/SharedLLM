import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { openAllDetails, screenFindings, settle, shot, type Allowed, quietHotReload } from '../fixtures/pageAudit';

/**
 * Settings, Admin (every tab), Identity and Remote on every form factor, with
 * their main dialogs and editors open: thumb-sized controls on a phone, no text
 * cut off, nothing drawn past its card or dialog, nothing past the edge of the
 * screen and no sideways page scroll. Hermetic: every call is answered by the
 * mocks (the catch-all stays empty) and no page error is thrown.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** Findings that are deliberate designs, each with its reason. */
const ALLOWED: Allowed[] = [];

interface Run {
  home: HomeMocks;
  errors: string[];
  findings: string[];
}

async function open(page: Page, path: string): Promise<Run> {
  await quietHotReload(page);
  const home = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockSettingsPages(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error' && /React UI error|Uncaught/.test(m.text())) errors.push(m.text().slice(0, 300)); });
  await page.goto(path);
  return { home, errors, findings: [] };
}

/** Audit one screen (the page's main, or an open dialog) after opening its folded sections. */
async function check(page: Page, info: TestInfo, run: Run, scope: Locator, where: string): Promise<void> {
  await openAllDetails(scope);
  await settle(page);
  run.findings.push(...await screenFindings(page, scope, where, ALLOWED));
  await shot(page, info, where.replace(/[^a-z0-9]+/gi, '-').toLowerCase());
}

/**
 * Open the dialog named `name` with `opener`, audit it, then close it with
 * Escape. Each dialog is found by its own name, so a previous one still fading
 * out is never mistaken for it.
 */
async function checkDialog(page: Page, info: TestInfo, run: Run, opener: Locator, where: string, name: string | RegExp): Promise<void> {
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const box = page.getByRole('dialog', { name });
  await opener.scrollIntoViewIfNeeded({ timeout: 10_000 });
  await opener.click({ timeout: 10_000 });
  await expect(box).toBeVisible();
  await settle(page);
  await check(page, info, run, box, where);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
}

function finish(run: Run): void {
  expect(run.findings).toEqual([]);
  expect([...new Set(run.home.unmocked)]).toEqual([]);
  expect(run.errors).toEqual([]);
}

async function adminTab(page: Page, label: string, ready: string | RegExp): Promise<void> {
  const tab = page.getByRole('tablist', { name: 'Admin sections' }).getByRole('tab', { name: label });
  await tab.scrollIntoViewIfNeeded();
  await tab.click();
  await expect(page.locator('main').getByText(ready).first()).toBeVisible();
}

test.describe('settings, admin, identity and remote on every form factor', () => {
  test.describe.configure({ timeout: 180_000 });

  test('settings', async ({ page }, info) => {
    const run = await open(page, '/settings');
    const main = page.locator('main');
    await expect(page.getByTestId('settings-profile-name')).toBeVisible();
    await expect(main.getByText('Jarvis Watch').first()).toBeVisible();
    await page.waitForTimeout(500);
    await check(page, info, run, main, 'settings');
    // The vehicle editor folds open inside the location card.
    await main.getByRole('button', { name: 'Manage Vehicles' }).click();
    await main.getByRole('button', { name: 'Add Vehicle' }).click();
    await expect(main.getByText('Your Vehicles')).toBeVisible();
    await check(page, info, run, main, 'settings vehicles');
    finish(run);
  });

  test('identity', async ({ page }, info) => {
    const run = await open(page, '/identity');
    const main = page.locator('main');
    await expect(main.getByText('Integration Gallery')).toBeVisible();
    await expect(main.getByText('OpenWebUI on the den computer')).toBeVisible();
    await page.waitForTimeout(500);
    await check(page, info, run, main, 'identity');
    await checkDialog(page, info, run, main.getByRole('button', { name: 'Manage Home Assistant' }), 'identity home assistant', 'Home Assistant');
    await checkDialog(page, info, run, main.getByRole('button', { name: /^(Connect|Manage) GitHub$/ }), 'identity github', 'GitHub');
    await checkDialog(page, info, run, main.getByRole('button', { name: 'Generate New Key' }), 'identity new api key', 'New API key');
    await checkDialog(page, info, run, main.getByTestId('avatar-open'), 'identity avatar', 'Choose a profile picture');
    await checkDialog(page, info, run, main.getByTestId('device-open-phone-1'), 'identity device', /^Details for/);
    finish(run);
  });

  test('remote', async ({ page }, info) => {
    const run = await open(page, '/remote');
    const main = page.locator('main');
    await expect(main.getByRole('button', { name: 'Up', exact: true })).toBeVisible();
    await check(page, info, run, main, 'remote');
    finish(run);
  });

  test('admin: users and devices', async ({ page }, info) => {
    const run = await open(page, '/admin/users');
    const main = page.locator('main');
    await expect(main.getByText('@robin').first()).toBeVisible();
    await page.waitForTimeout(500);
    await check(page, info, run, main, 'admin users');
    await checkDialog(page, info, run, page.getByRole('button', { name: 'Create User' }), 'admin create user', 'Create User');
    await checkDialog(page, info, run, main.getByRole('button', { name: 'Edit robin' }), 'admin edit user', 'Edit @robin');
    await checkDialog(page, info, run, main.getByRole('button', { name: 'Change password for robin' }), 'admin password', 'Set Password for @robin');
    await checkDialog(page, info, run, main.getByRole('button', { name: 'Shared service access for robin' }), 'admin shares', 'Shared Service Access for @robin');
    // The role editor opens under the list.
    await main.getByTestId('roles-panel').getByRole('button', { name: /Teen with a role name/ }).click();
    await expect(main.getByLabel('Role name')).toBeVisible();
    await check(page, info, run, main.getByTestId('roles-panel'), 'admin role editor');
    finish(run);
  });

  test('admin: groups, hardware, telemetry, storage and network', async ({ page }, info) => {
    const run = await open(page, '/admin/groups');
    const main = page.locator('main');
    await expect(main.getByText('Downstairs speakers', { exact: true }).first()).toBeVisible({ timeout: 30_000 });
    await check(page, info, run, main, 'admin groups media');
    await main.getByRole('button', { name: 'Light Clusters' }).click();
    await expect(main.getByText('Create Light Cluster')).toBeVisible();
    await check(page, info, run, main, 'admin groups lights');
    await main.getByRole('button', { name: 'Light Patterns' }).click();
    await expect(main.getByText('Create Light Pattern')).toBeVisible();
    await check(page, info, run, main, 'admin groups patterns');
    for (const [label, ready] of [['Hardware', /hardware/i], ['Telemetry', /telemetry/i], ['Storage', /storage/i], ['Network', /network/i]] as const) {
      await adminTab(page, label, ready);
      await page.waitForTimeout(500);
      await check(page, info, run, main, `admin ${label.toLowerCase()}`);
    }
    finish(run);
  });

  test('admin: intercom, raven, settings, database, services and bible', async ({ page }, info) => {
    const run = await open(page, '/admin/intercom');
    const main = page.locator('main');
    for (const sub of ['Sessions', 'Broadcast', 'Announce', 'Config']) {
      await main.getByRole('button', { name: sub, exact: true }).click();
      await page.waitForTimeout(300);
      await check(page, info, run, main, `admin intercom ${sub.toLowerCase()}`);
    }
    for (const [label, ready] of [['Raven Ops', /raven/i], ['LLM & Settings', /time zone/i], ['Database & Audit', 'a_setting_with_a_long_value'],
      ['System Services', 'System Services Health'], ['Bible', /bible/i]] as const) {
      await adminTab(page, label, ready);
      await page.waitForTimeout(500);
      await check(page, info, run, main, `admin ${label.toLowerCase()}`);
      if (label === 'Raven Ops') {
        await checkDialog(page, info, run, main.getByRole('button', { name: 'Audit log' }), 'admin raven audit log', 'Raven Audit Log');
        await checkDialog(page, info, run, main.getByRole('button', { name: 'Details' }).first(), 'admin raven mission', 'Mission #1');
      }
      if (label === 'Database & Audit') {
        await checkDialog(page, info, run, main.getByRole('button', { name: /View Data/ }).first(), 'admin inspect collection', /^Inspect:/);
      }
    }
    finish(run);
  });
});
