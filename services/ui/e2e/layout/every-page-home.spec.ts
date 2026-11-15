import { test, expect as baseExpect, type Locator, type Page, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { openAllDetails, screenFindings, settle, shot, type Allowed, quietHotReload } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The home pages (Devices, Rooms, Cameras, Climate, Schedules, Energy, History,
 * Lists, Automations with its step editor and helpers) on every form factor,
 * with every folded section open and each page's main dialogs and editors
 * opened too: nothing drawn past its card or dialog, no text cut off, no
 * control off the screen, no sideways scroll, and 44px targets on phones.
 * Hermetic: fixtures/homeMocks.ts answers every call and its catch-all stays
 * empty. The made-up house has long room, device and helper names on purpose.
 */

const ALLOWED: Allowed[] = [];

interface Run { mocks: HomeMocks; errors: string[]; found: string[] }

async function open(page: Page, path: string, ready: Locator | string = 'page-header'): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(path);
  await expect(typeof ready === 'string' ? page.getByTestId(ready) : ready).toBeVisible();
  await page.waitForLoadState('networkidle').catch(() => {});
  return { mocks, errors, found: [] };
}

/** Audit `scope` (the page's main area unless a dialog is given), with every section opened. */
async function check(page: Page, info: TestInfo, run: Run, where: string, scope?: Locator): Promise<void> {
  const target = scope ?? page.locator('main');
  await settle(page);
  await openAllDetails(target);
  run.found.push(...await screenFindings(page, target, where, ALLOWED));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

function done(run: Run) {
  expect(run.found, run.found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('every home page on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('Devices: every tab, with the reconfigure and add-device dialogs', async ({ page }, info) => {
    const run = await open(page, '/devices', 'devices-sections');
    await expect(page.getByTestId('hardware')).toBeVisible();
    for (const tab of ['Your devices', 'Add & fix', 'Rooms & places', 'Setup', 'Family']) {
      await page.getByTestId('devices-sections').getByRole('tab', { name: tab }).click();
      await page.waitForLoadState('networkidle').catch(() => {});
      await check(page, info, run, `devices ${tab}`);
    }
    await page.getByTestId('devices-sections').getByRole('tab', { name: 'Setup' }).click();
    await page.getByRole('button', { name: 'Reconfigure Music server' }).click();
    await check(page, info, run, 'devices reconfigure', page.getByTestId('reconfigure-dialog'));
    await page.getByTestId('reconfigure-dialog').getByRole('button', { name: 'Cancel' }).click();
    await page.getByTestId('devices-sections').getByRole('tab', { name: 'Add & fix' }).click();
    await page.getByRole('button', { name: /Add Matter device/ }).click();
    await expect(page.getByTestId('adopt-dialog')).toBeVisible();
    await check(page, info, run, 'devices add', page.getByRole('dialog').last());
    done(run);
  });

  test('Rooms', async ({ page }, info) => {
    const run = await open(page, '/rooms', 'home-overview');
    await expect(page.getByTestId('room-card')).toHaveCount(3);
    await check(page, info, run, 'rooms');
    done(run);
  });

  test('Cameras, the live view with its controls, and motion recording', async ({ page }, info) => {
    const run = await open(page, '/cameras', page.getByRole('button', { name: /Open Yard/ }));
    await check(page, info, run, 'cameras');
    await page.getByRole('button', { name: /Open Yard/ }).click();
    const viewer = page.getByRole('dialog', { name: 'Yard' });
    await expect(viewer.getByTestId('camera-controls')).toBeVisible();
    await check(page, info, run, 'cameras live', viewer);
    await page.keyboard.press('Escape');
    done(run);
  });

  test('Climate, with the hold sheet', async ({ page }, info) => {
    const run = await open(page, '/climate', 'thermostat-card');
    await check(page, info, run, 'climate');
    await page.getByTestId('thermostat-card').getByRole('button', { name: 'Hold Downstairs' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('hold-control')).toBeVisible();
    await check(page, info, run, 'climate hold', dialog);
    done(run);
  });

  test('Schedules, with away and energy', async ({ page }, info) => {
    const run = await open(page, '/schedules', 'thermostat-card');
    await check(page, info, run, 'schedules');
    await page.getByRole('button', { name: 'Away and energy' }).click();
    await check(page, info, run, 'schedules away');
    done(run);
  });

  test('Energy, with its settings', async ({ page }, info) => {
    const run = await open(page, '/energy');
    await expect(page.getByTestId('energy-kpi').first()).toContainText('18.4');
    await check(page, info, run, 'energy');
    await page.getByTestId('page-header').getByRole('button', { name: /Settings/ }).click();
    await expect(page.getByTestId('energy-settings')).toBeVisible();
    await check(page, info, run, 'energy settings');
    done(run);
  });

  test('History, a chart and the logbook', async ({ page }, info) => {
    const run = await open(page, '/history');
    await page.getByLabel('Search entities').fill('Down');
    await page.getByRole('option', { name: /Downstairs/ }).click();
    await expect(page.getByTestId('history-chip')).toHaveCount(1);
    await check(page, info, run, 'history chart');
    const logbook = page.getByRole('tab', { name: 'Logbook' });
    if (await logbook.count()) {
      await logbook.click();
      await expect(page.getByTestId('logbook-entry').first()).toBeVisible();
      await check(page, info, run, 'history logbook');
    }
    done(run);
  });

  test('Lists, with an item being edited', async ({ page }, info) => {
    const run = await open(page, '/lists');
    await expect(page.getByText('Book the boiler service')).toBeVisible();
    await check(page, info, run, 'lists');
    await page.getByRole('button', { name: 'Edit Book the boiler service' }).click();
    await check(page, info, run, 'lists edit');
    done(run);
  });

  test('Automations: the list, the step editor, blueprints and helpers', async ({ page }, info) => {
    const run = await open(page, '/automations');
    await expect(page.getByRole('button', { name: 'Open Porch light at dusk' })).toBeVisible();
    await check(page, info, run, 'automations');

    // A fresh page for the editor, so nothing opened by the audit above is still open.
    await page.goto('/automations');
    await page.getByRole('button', { name: 'Open Porch light at dusk' }).click();
    const editor = page.getByTestId('automation-editor');
    await expect(editor).toBeVisible();
    const addAction = editor.getByRole('combobox', { name: 'Add action' }).last();
    for (const type of ['repeat', 'parallel', 'wait_for_trigger', 'condition', 'set_conversation_response']) {
      await editor.getByRole('combobox', { name: 'Add action' }).last().selectOption(type);
      await addAction.locator('xpath=following-sibling::button').click();
    }
    const addTrigger = editor.getByRole('combobox', { name: 'Add trigger' }).last();
    if (await addTrigger.count()) {
      for (const type of ['time', 'conversation']) {
        await addTrigger.selectOption(type);
        await addTrigger.locator('xpath=following-sibling::button').click();
      }
    }
    await expect(editor.getByText('All at the same time')).toBeVisible();
    await editor.getByRole('button', { name: /^Variables/ }).click();
    await editor.getByRole('button', { name: 'Add variable' }).click();
    await check(page, info, run, 'automation editor', editor);
    await page.getByRole('tab', { name: 'YAML' }).click();
    await check(page, info, run, 'automation yaml', editor);
    await page.keyboard.press('Escape');

    await page.goto('/automations');
    await page.getByRole('button', { name: 'New' }).click();
    await page.getByRole('button', { name: 'From a blueprint' }).click();
    await page.getByTestId('blueprint-list').getByRole('button', { name: /Motion-activated light/ }).click();
    await expect(page.getByTestId('blueprint-form')).toBeVisible();
    await check(page, info, run, 'automation blueprint', page.getByTestId('automation-editor'));

    await page.goto('/automations');
    await page.getByRole('button', { name: 'Helpers' }).click();
    const panel = page.getByTestId('helpers-panel');
    await expect(panel.getByTestId('helper-row')).toHaveCount(3);
    await check(page, info, run, 'helpers', panel);
    await panel.getByRole('button', { name: /Add helper/ }).click();
    await page.getByTestId('helper-types').getByRole('button', { name: /Timer/ }).click();
    await expect(page.getByTestId('helper-form')).toBeVisible();
    await check(page, info, run, 'helper form', page.getByTestId('helper-form'));
    done(run);
  });
});
