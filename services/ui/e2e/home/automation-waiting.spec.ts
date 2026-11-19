import { test, expect as baseExpect } from '@playwright/test';
import { AUTOMATION, mockHome } from '../fixtures/homeMocks';
import { expectLayoutSound, expectNoHorizontalOverflow, expectTapTargets } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * An automation that calls a device's own action while no connected device offers it is listed as waiting
 * for its device: a calm badge with the reason, its switch still on, no run button and no error. Checked in
 * every hermetic project (desktop, phone, small phone, tablet both ways round). Names are made up.
 */

const WAITING = {
  ...AUTOMATION, id: 'screen_sync', entity_id: 'automation.screen_sync', alias: 'Screen playlists sync',
  last_triggered: null, entities: [],
  waiting_reason: 'needs esphome.wall_screen_set_playlists, which no connected device offers',
};

test('automations: one waiting for its device', async ({ page }, info) => {
  const mocks = await mockHome(page, 'admin', { automations: [AUTOMATION, WAITING] });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/automations');
  await expect(page.getByTestId('page-header')).toBeVisible();
  await expect(page.getByTestId('automation-row')).toHaveCount(2);

  const row = page.getByTestId('automation-row').filter({ hasText: 'Screen playlists sync' });
  const badge = row.getByTestId('automation-waiting');
  await expect(badge).toContainText('Waiting for its device');
  await expect(badge).toContainText(WAITING.waiting_reason);
  await expect(row.getByRole('switch', { name: 'Screen playlists sync enabled' })).toBeChecked();
  await expect(row.getByRole('button', { name: 'Run Screen playlists sync now' })).toHaveCount(0);
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
