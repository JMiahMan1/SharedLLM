import { test, expect as baseExpect } from '@playwright/test';
import { AUTOMATION, mockHome } from '../fixtures/homeMocks';
import { expectLayoutSound, expectNoHorizontalOverflow, expectTapTargets } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * An automation that names a device's own action while no connected device offers it is listed with a
 * "Device away" badge and the reason, its switch still on, its run error in red, and a Run now button: its
 * runs still start and fail at that step. Checked in every hermetic project (desktop, phone, small phone,
 * tablet both ways round). Names are made up.
 */

const WAITING = {
  ...AUTOMATION, id: 'screen_sync', entity_id: 'automation.screen_sync', alias: 'Screen playlists sync',
  last_triggered: null, entities: [],
  last_error: 'esphome.wall_screen_set_playlists is not offered: its device is not connected',
  waiting_reason: 'needs esphome.wall_screen_set_playlists, which no connected device offers: a run that reaches it fails at that step',
};

test('automations: one whose device is away', async ({ page }, info) => {
  const mocks = await mockHome(page, 'admin', { automations: [AUTOMATION, WAITING] });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/automations');
  await expect(page.getByTestId('page-header')).toBeVisible();
  await expect(page.getByTestId('automation-row')).toHaveCount(2);

  const row = page.getByTestId('automation-row').filter({ hasText: 'Screen playlists sync' });
  const badge = row.getByTestId('automation-waiting');
  await expect(badge).toContainText('Device away');
  await expect(badge).toContainText(WAITING.waiting_reason);
  await expect(row).toContainText(WAITING.last_error);
  await expect(row.getByRole('switch', { name: 'Screen playlists sync enabled' })).toBeChecked();
  await expect(row.getByRole('button', { name: 'Run Screen playlists sync now' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run Porch light at dusk now' })).toBeVisible();

  // The reason wraps inside the row instead of pushing the page sideways on a small phone.
  const rowBox = await row.boundingBox();
  const badgeBox = await badge.boundingBox();
  expect(rowBox && badgeBox && badgeBox.x + badgeBox.width <= rowBox.x + rowBox.width + 1).toBe(true);
  await expectNoHorizontalOverflow(page);
  await expectTapTargets(row);
  await expectLayoutSound(page, info, 'automation-waiting');

  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
});
