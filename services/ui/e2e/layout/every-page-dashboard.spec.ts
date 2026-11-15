import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockDashboard } from '../fixtures/dashboardMocks';
import { openAllDetails, screenFindings, settle, shot, type Allowed, quietHotReload } from '../fixtures/pageAudit';
import { snapScrolling } from '../fixtures/screenAudit';

/**
 * The Dashboard ("/") on every form factor, with every widget turned on and
 * filled with made-up data (long names, long titles, an unbroken long word),
 * then each widget's full-screen view and the page's dialogs and forms: the
 * settings panel, a widget's menu, a service's details, adding a chore, the
 * timer options, editing the note and the picture, a device's and a
 * thermostat's details, the climate devices sheet and a knowledge search.
 *
 * Every screen gets the shared audit (44px targets on phones, no hard-clipped
 * text, nothing drawn past its card or dialog, nothing off the screen, no
 * sideways scroll). Findings are gathered and asserted once at the end, so a
 * run reports every problem. Hermetic: every call is answered.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** Deliberate designs the audit would otherwise report (each says why). */
const ALLOWED: Allowed[] = [
  { where: /./, finding: /under 44px: Directions \(/,
    why: 'A link inside the person\'s own markdown note is inline text (the WCAG 2.5.8 inline exception); padding it would break the line it sits in' },
];

/** The widgets that open a full-screen view. */
const EXPANDABLE = ['chores_progress', 'upcoming_events', 'device_control', 'note'];

async function open(page: Page) {
  await quietHotReload(page);
  const home = await mockHome(page, 'admin');
  const unknownHealth = await mockHealth(page);
  const unknownBible = await mockBible(page);
  await mockDashboard(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error' && /React UI error|Uncaught/.test(m.text())) errors.push(m.text().slice(0, 300)); });
  await page.goto('/');
  // Real content, not skeletons: a chore, a timer, an event, a device and the services.
  const main = page.locator('main');
  await expect(main.getByText('Unload the dishwasher')).toBeVisible();
  await expect(main.getByText('Pasta', { exact: true })).toBeVisible();
  await expect(main.getByText('Dentist')).toBeVisible();
  await expect(main.locator('[data-widget="device_control"]').getByText('Garage door')).toBeVisible();
  await expect(main.getByText('Homehub')).toBeVisible();
  await expect(main.getByText('Tales From The Old Mill', { exact: false })).toBeVisible();
  await settle(page);
  return { home, unknownHealth, unknownBible, errors };
}

async function check(page: Page, info: TestInfo, scope: Locator, where: string, out: string[]) {
  await settle(page);
  await openAllDetails(scope);
  out.push(...await screenFindings(page, scope, where, ALLOWED));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

async function finish(run: Awaited<ReturnType<typeof open>>, findings: string[]) {
  expect(findings).toEqual([]);
  expect(run.home.unmocked).toEqual([]);
  expect(run.unknownHealth).toEqual([]);
  expect(run.unknownBible).toEqual([]);
  expect(run.errors).toEqual([]);
}

const widget = (page: Page, key: string) => page.locator(`[data-widget="${key}"]`);

test.describe('the dashboard on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('every widget, filled in', async ({ page }, info) => {
    const run = await open(page);
    const findings: string[] = [];
    // Every widget is on the board.
    await expect(page.locator('[data-widget]')).toHaveCount(18);
    await check(page, info, page.locator('main'), 'the dashboard', findings);
    await snapScrolling(page, info, `dashboard-${info.project.name}`, 14);
    await finish(run, findings);
  });

  test('each full-screen view', async ({ page }, info) => {
    const run = await open(page);
    const findings: string[] = [];
    for (const key of EXPANDABLE) {
      await widget(page, key).getByRole('button', { name: 'Expand widget' }).click();
      const view = page.getByTestId('widget-full-view');
      await expect(view).toBeVisible();
      await check(page, info, view, `the ${key} full view`, findings);
      await view.getByRole('button', { name: 'Collapse widget' }).first().click();
      await expect(view).toHaveCount(0);
    }
    await finish(run, findings);
  });

  test('the dashboard dialogs', async ({ page }, info) => {
    const run = await open(page);
    const findings: string[] = [];
    const dialog = page.getByRole('dialog');

    await page.getByRole('button', { name: 'Dashboard settings' }).click();
    await expect(dialog).toBeVisible();
    await check(page, info, dialog, 'the dashboard settings', findings);
    await dialog.getByRole('button', { name: 'Reset All' }).click();
    await expect(dialog.getByRole('alertdialog')).toBeVisible();
    await check(page, info, dialog, 'confirming a reset', findings);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await dialog.getByRole('button', { name: 'Close settings' }).click();
    await expect(dialog).toHaveCount(0);

    await widget(page, 'weather').getByRole('button', { name: 'Widget options' }).click();
    const menu = page.getByRole('menu');
    await expect(menu).toBeVisible();
    await check(page, info, menu, 'a widget menu', findings);
    await page.keyboard.press('Escape');
    if (await menu.count()) await page.mouse.click(2, 2);
    await expect(menu).toHaveCount(0);

    await page.locator('main').getByRole('button', { name: 'View workspace runtime service details' }).click();
    await expect(dialog).toBeVisible();
    await check(page, info, dialog, 'a service details', findings);
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);

    await page.getByRole('textbox', { name: 'Search the knowledge base' }).fill('garden');
    await expect(page.getByText('Live Search Result')).toBeVisible();
    await check(page, info, page.locator('main'), 'a knowledge search', findings);
    await page.getByRole('button', { name: 'Close search results' }).click();

    await finish(run, findings);
  });

  test('the widget forms', async ({ page }, info) => {
    const run = await open(page);
    const findings: string[] = [];
    const dialog = page.getByRole('dialog');

    await widget(page, 'chores_progress').getByTestId('chore-add').click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Jordan' })).toBeVisible();
    await check(page, info, dialog, 'adding a chore', findings);
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);

    await widget(page, 'ambient_timer').getByRole('button', { name: 'Timer & alarm options' }).click();
    await check(page, info, widget(page, 'ambient_timer'), 'the timer options', findings);

    await widget(page, 'note').getByRole('button', { name: 'Edit note' }).click();
    await expect(page.getByTestId('note-editor')).toBeVisible();
    await check(page, info, widget(page, 'note'), 'editing the note', findings);

    await widget(page, 'picture').getByRole('button', { name: 'Change picture' }).click();
    await expect(widget(page, 'picture').getByLabel('Picture link')).toBeVisible();
    await check(page, info, widget(page, 'picture'), 'changing the picture', findings);

    await finish(run, findings);
  });

  test('the device and climate sheets', async ({ page }, info) => {
    const run = await open(page);
    const findings: string[] = [];
    await widget(page, 'device_control').getByText('Garage door').click();
    const device = page.getByRole('dialog', { name: 'Device details' });
    await expect(device).toBeVisible();
    await check(page, info, device, 'a device details', findings);
    await device.getByRole('button', { name: 'Close device details' }).first().click();
    await expect(device).toHaveCount(0);

    await widget(page, 'device_control').getByText(/^Living room reading lamp/).click();
    await expect(device).toBeVisible();
    await check(page, info, device, 'a light details', findings);
    await device.getByRole('button', { name: 'Close device details' }).first().click();
    await expect(device).toHaveCount(0);

    await widget(page, 'device_control').getByRole('button', { name: /^All/ }).click();
    for (const [name, where] of [['Upstairs bonus room over the garage', 'a thermostat details'], ['Den TV', 'a media player details'],
      ['Bedroom ceiling fan', 'a fan details']]) {
      // The rows sit low in a scrolling card that can end under the tablet's fold: tap the row itself.
      await widget(page, 'device_control').getByText(name, { exact: true }).dispatchEvent('click');
      await expect(device).toBeVisible();
      await check(page, info, device, where, findings);
      await device.getByRole('button', { name: 'Close device details' }).first().click();
      await expect(device).toHaveCount(0);
    }

    await widget(page, 'climate').getByRole('button', { name: /Devices/ }).click();
    const sheet = page.getByRole('dialog', { name: 'Climate widget devices' });
    await expect(sheet).toBeVisible();
    await check(page, info, sheet, 'the climate devices', findings);
    await sheet.getByRole('button', { name: 'Close' }).click();

    await finish(run, findings);
  });
});
