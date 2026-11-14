import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { CONTROLS_ROOMS, controlsDetail } from '../fixtures/entityControlMocks';
import { HARDWARE } from '../fixtures/hardwareMocks';
import {
  controlsOffScreen, expectFits, expectNoHorizontalOverflow, expectTapTargets, snap,
} from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Buttons, numbers, selects, times, remotes, update entities, image entities and a vacuum's rooms,
 * on a room card and in a device's sheet, in every project (desktop, Pixel 7, a small phone, a tablet
 * both ways round). Hermetic: homeMocks.ts plus entityControlMocks.ts.
 */

type Run = { mocks: HomeMocks; errors: string[] };

async function open(page: Page, path: string): Promise<Run> {
  const mocks = await mockHome(page, 'admin');
  // Registered after homeMocks, so these answer first.
  await page.route('**/api/dashboard/rooms', (route) => route.fulfill({ json: CONTROLS_ROOMS }));
  // The device sheet re-reads the device after a change: answer with what the hub would now hold.
  await page.route('**/api/homehub/hardware/*', (route) => {
    const skip = mocks.posts.some((p) => (p.body as { service?: string } | null)?.service === 'skip');
    return route.fulfill({ json: controlsDetail(HARDWARE.devices[0], skip ? '1.1.0' : null) });
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(path);
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors };
}

function clean({ mocks, errors }: Run) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

/** The ha_service calls the page made, as {domain, service, entity_id, service_data}. */
function calls(run: Run) {
  return run.mocks.posts.filter((p) => p.path === '/execute/ha_service')
    .map((p) => p.body as { domain: string; service: string; entity_id: string; service_data: Record<string, unknown> | null });
}

async function expectSound(page: Page) {
  await expectNoHorizontalOverflow(page);
  expect(await controlsOffScreen(page), 'controls past the edge of the screen').toEqual([]);
}

test.describe('entity controls', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a room card presses, sets, picks, sends keys, cleans rooms and shows the map', async ({ page }, info) => {
    const run = await open(page, '/rooms');
    const card = page.getByTestId('room-card');
    await expect(card).toHaveCount(1);
    await card.scrollIntoViewIfNeeded();

    // Button
    await card.getByRole('button', { name: 'Press Door chime' }).click();
    await expect.poll(() => calls(run).some((c) => c.domain === 'button' && c.service === 'press'
      && c.entity_id === 'button.utility_chime')).toBe(true);

    // Number: the stepper sends one set_value after the taps settle, on the device's grid.
    await expect(card.getByLabel('Fan level value')).toHaveText('40 %');
    await card.getByRole('button', { name: 'Raise Fan level' }).click();
    await card.getByRole('button', { name: 'Raise Fan level' }).click();
    await expect(card.getByLabel('Fan level value')).toHaveText('60 %');
    await expect.poll(() => calls(run).find((c) => c.service === 'set_value' && c.domain === 'number')?.service_data)
      .toEqual({ value: 60 });
    await expect(card.getByRole('slider', { name: 'Fan level level' })).toBeVisible();

    // Select
    await card.getByLabel('Washer program').selectOption('delicates');
    await expect.poll(() => calls(run).find((c) => c.service === 'select_option')?.service_data)
      .toEqual({ option: 'delicates' });

    // Time: nothing is sent until Set.
    await card.getByLabel('Quiet hours start').fill('21:15');
    expect(calls(run).some((c) => c.domain === 'time')).toBe(false);
    await card.getByRole('button', { name: 'Set Quiet hours start' }).click();
    await expect.poll(() => calls(run).find((c) => c.domain === 'time')?.service_data).toEqual({ time: '21:15:00' });

    // Remote: a pad of the keys it lists, its activities, and any other key it knows.
    const tv = card.locator('[data-testid="remote-control"][data-entity="remote.utility_tv"]');
    await tv.getByRole('button', { name: 'Keys' }).click();
    await tv.getByRole('button', { name: 'Up on Utility TV' }).click();
    await expect.poll(() => calls(run).find((c) => c.service === 'send_command')?.service_data).toEqual({ command: 'up' });
    await tv.getByLabel('Utility TV command').selectOption('volume_up');
    await tv.getByRole('button', { name: 'Send' }).click();
    await expect.poll(() => calls(run).filter((c) => c.service === 'send_command').at(-1)?.service_data)
      .toEqual({ command: 'volume_up' });
    await tv.getByLabel('Utility TV activity').selectOption('Music');
    await expect.poll(() => calls(run).find((c) => c.domain === 'remote' && c.service === 'turn_on')?.service_data)
      .toEqual({ activity: 'Music' });
    await expectTapTargets(tv);
    // A remote that lists no keys gets a field to type one.
    const box = card.locator('[data-testid="remote-control"][data-entity="remote.utility_old_box"]');
    await box.getByRole('button', { name: 'Keys' }).click();
    await expect(box.getByRole('button', { name: /on Old box/ })).toHaveCount(0);
    await box.getByLabel('Old box command').fill('input_hdmi1');
    await box.getByRole('button', { name: 'Send' }).click();
    await expect.poll(() => calls(run).filter((c) => c.entity_id === 'remote.utility_old_box').at(-1)?.service_data)
      .toEqual({ command: 'input_hdmi1' });

    // Vacuum: pick rooms, clean them.
    const rooms = card.getByTestId('vacuum-rooms');
    await rooms.getByRole('button', { name: 'Clean rooms' }).click();
    await expect(rooms.getByRole('button', { name: 'Pick rooms to clean' })).toBeDisabled();
    await rooms.getByRole('button', { name: 'Kitchen' }).click();
    await rooms.getByRole('button', { name: 'Hallway' }).click();
    await expect(rooms.getByRole('button', { name: 'Kitchen' })).toHaveAttribute('aria-pressed', 'true');
    await expectTapTargets(rooms);
    await rooms.getByRole('button', { name: 'Clean 2 rooms' }).click();
    await expect.poll(() => calls(run).find((c) => c.domain === 'vacuum')?.service_data)
      .toEqual({ command: 'app_segment_clean', params: [{ segments: [16, 17], repeat: 1 }] });

    // Image: the map, through the signed media route.
    const map = card.getByTestId('image-tile');
    await map.scrollIntoViewIfNeeded();
    const img = map.getByRole('img', { name: 'Robot map' });
    await expect(img).toBeVisible();
    await expect.poll(() => img.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    expect(await img.getAttribute('src')).toMatch(/\/api\/media\/images\/image\.utility_robot_map\?user=parent&mt=/);

    await expectTapTargets(card.getByRole('region', { name: 'Buttons' }));
    await expectTapTargets(card.getByRole('region', { name: 'Choices' }));
    await expectTapTargets(card.getByRole('region', { name: 'Times' }));
    await expectSound(page);
    await snap(page, info, 'entity-controls-room');
    clean(run);
  });

  test('a device sheet shows versions and offers install only where the hub can', async ({ page }, info) => {
    const run = await open(page, '/devices');
    const panel = page.getByTestId('hardware');
    await panel.scrollIntoViewIfNeeded();
    await panel.getByTestId('hardware-device').filter({ hasText: 'Reading lamp' }).click();
    const dialog = page.getByRole('dialog');
    const detail = dialog.getByTestId('hardware-detail');
    await expect(detail).toBeVisible();
    // Firmware, restart and the LED level are settings: in the sheet, folded away.
    await detail.getByRole('button', { name: /Show settings, diagnostics and disabled \(4\)/ }).click();

    const fw = detail.locator('[data-testid="update-control"][data-entity="update.porch_node_firmware"]');
    await expect(fw).toContainText('2026.10.2 available (2026.9.0 installed)');
    await expect(fw.getByRole('link', { name: 'Release notes' })).toHaveAttribute('href', 'https://example.com/changelog/');
    await expectTapTargets(fw);
    await fw.getByRole('button', { name: 'Install 2026.10.2 on Porch node Firmware' }).click();
    await expect.poll(() => calls(run).find((c) => c.service === 'install')?.entity_id).toBe('update.porch_node_firmware');

    const bulb = detail.locator('[data-testid="update-control"][data-entity="update.porch_node_bulb_firmware"]');
    await expect(bulb).toContainText('1.1.0 available (1.0.0 installed)');
    await expect(bulb.getByRole('button', { name: /^Install/ })).toHaveCount(0);
    await expect(bulb).toContainText('Install it from the device');
    await expect(bulb).toContainText('Fixes a long-standing flicker');
    await bulb.getByRole('button', { name: 'Skip 1.1.0 on Bulb Firmware' }).click();
    await expect.poll(() => calls(run).find((c) => c.service === 'skip')?.entity_id).toBe('update.porch_node_bulb_firmware');
    await expect(bulb.getByRole('button', { name: 'Stop skipping 1.1.0 on Bulb Firmware' })).toBeVisible();

    // A box-mode number keeps the device's grid (0.1 steps).
    await expect(detail.getByLabel('LED brightness value')).toHaveText('0.4');
    await detail.getByRole('button', { name: 'Lower LED brightness' }).click();
    await expect(detail.getByLabel('LED brightness value')).toHaveText('0.3');
    await expect(detail.getByRole('slider', { name: 'LED brightness level' })).toHaveCount(0);
    await detail.getByRole('button', { name: 'Press Restart' }).click();
    await expect.poll(() => calls(run).some((c) => c.entity_id === 'button.porch_node_restart')).toBe(true);

    await expectFits(page, dialog);
    await expectSound(page);
    await snap(page, info, 'entity-controls-device-sheet');
    clean(run);
  });
});
