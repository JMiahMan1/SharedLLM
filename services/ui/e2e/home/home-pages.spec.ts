import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks, type Role } from '../fixtures/homeMocks';
import {
  expectFits, expectLayoutSound, expectNavFor, expectNoHorizontalOverflow, expectTapTargets, isPhoneShell, snap,
} from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The home pages built for homehub (Rooms, Climate, Cameras, Energy, History,
 * Automations, Schedules, Devices) and the grouped nav, in every project:
 * desktop, Pixel 7, a small phone, and a tablet both ways round. Hermetic:
 * every call is answered by fixtures/homeMocks.ts.
 */

async function open(page: Page, role: Role, path: string): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, role);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(path);
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors };
}

function clean({ mocks, errors }: { mocks: HomeMocks; errors: string[] }) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('home pages', () => {
  test.describe.configure({ timeout: 90_000 });

  test('Rooms: the home overview, rooms by floor, one-tap room off', async ({ page }, info) => {
    const run = await open(page, 'admin', '/rooms');
    const glance = page.getByTestId('home-overview');
    await expect(glance.getByText('3 lights on in 2 rooms')).toBeVisible();
    await expect(glance.getByText('Following Weekdays: 74, goes to 78 at 5 PM')).toBeVisible();
    await expect(glance.getByTestId('glance-energy')).toContainText('18.4');
    await expect(page.getByTestId('room-card')).toHaveCount(3);
    await expectTapTargets(glance);
    await expectLayoutSound(page, info, 'rooms');
    await glance.getByRole('button', { name: 'Turn off the lights in Living room' }).click();
    await expect(glance.getByText('1 light on in 1 room')).toBeVisible();
    expect(run.mocks.posts.filter((p) => p.path === '/execute/ha_service').length).toBeGreaterThan(0);
    await expectNavFor(page, true);
    clean(run);
  });

  test('Rooms for a member: no admin pages in the nav', async ({ page }, info) => {
    const run = await open(page, 'member', '/rooms');
    await expect(page.getByTestId('home-overview')).toBeVisible();
    await expectNavFor(page, false);
    await expectLayoutSound(page, info, 'rooms-member');
    clean(run);
  });

  test('Climate: schedule line, hold in a sheet that fits, resume', async ({ page }, info) => {
    const run = await open(page, 'admin', '/climate');
    const card = page.getByTestId('thermostat-card');
    await expect(card.getByText('Following Weekdays: 74, goes to 78 at 5 PM')).toBeVisible();
    await expectTapTargets(card.getByTestId('schedule-status'));
    await expectLayoutSound(page, info, 'climate');
    await card.getByRole('button', { name: 'Hold Downstairs' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('hold-control')).toBeVisible();
    await expectFits(page, dialog);
    await snap(page, info, 'climate-hold');
    await dialog.getByRole('button', { name: /^Hold$/ }).click();
    await expect.poll(() => run.mocks.posts.some((p) => p.path === '/api/homehub/climate/hold')).toBe(true);
    clean(run);
  });

  test('Cameras: grid, full-screen live view, recordings tab', async ({ page }, info) => {
    const run = await open(page, 'admin', '/cameras');
    await expect(page.getByRole('button', { name: /Open Yard/ })).toBeVisible();
    await expectLayoutSound(page, info, 'cameras');
    await page.getByRole('button', { name: /Open Yard/ }).click();
    const viewer = page.getByRole('dialog', { name: 'Yard' });
    await expect(viewer).toBeVisible();
    await expectFits(page, viewer);
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(viewer.getByRole('tablist'));
    await snap(page, info, 'cameras-live');
    await viewer.getByRole('tab', { name: 'Recordings' }).click();
    await expect(page).toHaveURL(/view=recordings/);
    await snap(page, info, 'cameras-recordings');
    await page.keyboard.press('Escape');
    await expect(viewer).toHaveCount(0);
    clean(run);
  });

  test('Cameras for a member: no live video settings', async ({ page }) => {
    const run = await open(page, 'member', '/cameras');
    await expect(page.getByRole('button', { name: /Open Yard/ })).toBeVisible();
    await expect(page.getByText('Live video settings')).toHaveCount(0);
    clean(run);
  });

  test('Energy: numbers, settings and the long-term history card for an admin', async ({ page }, info) => {
    const run = await open(page, 'admin', '/energy');
    await expect(page.getByTestId('energy-kpi').first()).toContainText('18.4');
    await expectLayoutSound(page, info, 'energy');
    await page.getByTestId('page-header').getByRole('button', { name: /Settings/ }).click();
    await expect(page.getByTestId('energy-settings')).toBeVisible();
    await expect(page.getByTestId('statistics-status')).toContainText('42 sensors');
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'energy-settings');
    clean(run);
  });

  test('History: empty state, then a chart from the picker', async ({ page }, info) => {
    const run = await open(page, 'admin', '/history');
    await expect(page.getByTestId('empty-state')).toContainText('Pick something to chart');
    await page.getByLabel('Search entities').fill('Down');
    await page.getByRole('option', { name: /Downstairs/ }).click();
    await expect(page.getByTestId('history-chip')).toHaveCount(1);
    await expectLayoutSound(page, info, 'history');
    clean(run);
  });

  test('Automations: list, editor, YAML mode, Repairs', async ({ page }, info) => {
    const run = await open(page, 'admin', '/automations');
    await expect(page.getByRole('button', { name: 'Open Porch light at dusk' })).toBeVisible();
    await expectLayoutSound(page, info, 'automations');
    await page.getByRole('button', { name: 'Open Porch light at dusk' }).click();
    await page.getByRole('tab', { name: 'YAML' }).click();
    await expect(page.getByRole('tab', { name: 'YAML' })).toHaveAttribute('aria-selected', 'true');
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'automations-yaml');
    await page.keyboard.press('Escape');
    clean(run);
  });

  test('Schedules: thermostat timeline, hold and away', async ({ page }, info) => {
    const run = await open(page, 'admin', '/schedules');
    const card = page.getByTestId('thermostat-card');
    await expect(card).toBeVisible();
    await expectLayoutSound(page, info, 'schedules');
    await page.getByRole('button', { name: 'Away and energy' }).click();
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'schedules-away');
    clean(run);
  });

  test('Devices: section jumps, new devices, Add dialog, integrations health, phones', async ({ page }, info) => {
    const run = await open(page, 'admin', '/devices');
    await expect(page.getByTestId('devices-sections')).toBeVisible();
    await expect(page.getByTestId('integrations-health')).toContainText('Music server');
    await expectTapTargets(page.getByTestId('devices-sections'));
    await expectLayoutSound(page, info, 'devices');
    // Phones: charging and the next alarm from the Jarvis app, on every screen size.
    const phone = page.getByTestId('phones').getByTestId('phone');
    await phone.scrollIntoViewIfNeeded();
    await expect(phone.getByTestId('phone-power')).toHaveText('Charging (wireless)');
    await expect(phone.getByTestId('phone-alarm')).toContainText('Next alarm');
    for (const line of [phone.getByTestId('phone-power'), phone.getByTestId('phone-alarm')]) {
      const box = await line.boundingBox();
      expect((box?.x ?? 0) + (box?.width ?? 0), 'the status line stays on screen').toBeLessThanOrEqual(
        (page.viewportSize()?.width ?? 0) + 1);
    }
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-phones');
    await page.getByRole('button', { name: /Add Matter device/ }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('adopt-dialog')).toBeVisible();
    await expect(dialog.getByLabel(/Room/)).toBeVisible();
    // The sheet slides up as it opens: measure it once it has come to rest.
    const height = page.viewportSize()?.height ?? 0;
    await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.y + b.height : height + 99; })
      .toBeLessThanOrEqual(height + 1);
    await expectFits(page, dialog);
    await snap(page, info, 'devices-add');
    clean(run);
  });

  test('locked mode: the locked shell keeps its bottom bar on screen', async ({ page }, info) => {
    const run = await open(page, 'member', '/rooms');
    await page.evaluate(() => (window as unknown as { __jarvisSetLockedMode: (s: unknown) => void })
      .__jarvisSetLockedMode({ active: true, sections: ['chat', 'calls', 'bible'] }));
    await expect(page.getByTestId('locked-shell')).toBeVisible();
    const nav = page.getByTestId('locked-nav');
    await expect(nav).toBeVisible();
    const box = await nav.boundingBox();
    const height = page.viewportSize()?.height ?? 0;
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(height + 1);
    await expectTapTargets(nav);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'locked');
    expect(run.errors).toEqual([]);
  });

  test('the phone bottom bar stays at five pages plus More', async ({ page }) => {
    await open(page, 'admin', '/rooms');
    test.skip(!(await isPhoneShell(page)), 'desktop shows the sidebar');
    const bar = page.getByRole('button', { name: 'More pages' }).locator('..');
    await expect(bar.getByRole('link')).toHaveCount(5);
  });
});
