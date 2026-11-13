import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > Hardware, moved devices and adding by hand, in every project (desktop, Pixel 7, a
 * small phone, a tablet both ways round). Hermetic: fixtures/homeMocks.ts + hardwareMocks.ts.
 */

async function open(page: Page): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/devices');
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors };
}

/** A sheet slides up as it opens; measure it once it has stopped moving. */
async function settled(page: Page, dialog: ReturnType<Page['getByRole']>) {
  const h = page.viewportSize()?.height ?? 0;
  await expect.poll(async () => (await dialog.boundingBox())?.y ?? -1).toBeGreaterThanOrEqual(0);
  await expect.poll(async () => { const b = await dialog.boundingBox(); return b ? b.y + b.height : h + 99; })
    .toBeLessThanOrEqual(h + 1);
}

function clean({ mocks, errors }: { mocks: HomeMocks; errors: string[] }) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('Devices: the hardware list', () => {
  test.describe.configure({ timeout: 90_000 });

  test('lists, filters and groups devices, and opens one with its controls', async ({ page }, info) => {
    const run = await open(page);
    const panel = page.getByTestId('hardware');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByTestId('hardware-device')).toHaveCount(3);
    await expect(panel.getByRole('button', { name: 'Living TV, Offline' })).toBeVisible();
    await expectTapTargets(panel.getByTestId('hardware-filters'));
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-hardware');

    await panel.getByLabel('Search devices').fill('kettle');
    await expect(panel.getByTestId('hardware-device')).toHaveCount(1);
    await panel.getByLabel('Search devices').fill('');
    await panel.getByTestId('hardware-filters').getByRole('button', { name: /Offline 1/ }).click();
    await expect(panel.getByTestId('hardware-device')).toHaveCount(1);
    await panel.getByTestId('hardware-filters').getByRole('button', { name: 'All' }).click();
    await panel.getByLabel('Group devices by').selectOption('integration');
    await expect(panel.getByTestId('hardware-group')).toHaveCount(3);

    await panel.getByTestId('hardware-device').filter({ hasText: 'Reading lamp' }).click();
    const dialog = page.getByRole('dialog');
    const detail = dialog.getByTestId('hardware-detail');
    await expect(detail).toBeVisible();
    await expect(detail.getByText('02:00:00:00:00:21')).toBeVisible();
    await expect(detail.getByTestId('hardware-abilities')).toContainText('Dim');
    await expect(detail.getByRole('slider', { name: 'Reading lamp brightness' })).toBeVisible();
    await settled(page, dialog);
    await expectFits(page, dialog);
    await snap(page, info, 'devices-hardware-detail');
    await detail.getByRole('switch', { name: 'Toggle Reading lamp' }).click();
    await expect.poll(() => run.mocks.posts.some((p) => p.path === '/execute/ha_service')).toBe(true);
    await detail.getByRole('button', { name: 'Rename Reading lamp' }).click();
    await detail.getByLabel('New name for Reading lamp').fill('Chair lamp');
    await detail.getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => run.mocks.posts.find(
      (p) => decodeURIComponent(p.path) === '/api/homehub/entities/light.reading_lamp')?.body)
      .toEqual({ name: 'Chair lamp' });
    clean(run);
  });

  test('a device at a new address is offered as an update; adding by hand is grouped', async ({ page }, info) => {
    const run = await open(page);
    await page.getByRole('tab', { name: 'Add & fix' }).click();
    const moved = page.getByTestId('moved-device');
    await moved.scrollIntoViewIfNeeded();
    await expect(moved).toContainText('192.0.2.66 → 192.0.2.96');
    await expectTapTargets(page.getByTestId('moved-devices'));
    await moved.getByRole('button', { name: 'Use the new address for Living TV' }).click();
    await expect.poll(() => run.mocks.posts.some(
      (p) => decodeURIComponent(p.path) === '/api/homehub/discovered/roku:x00000000001/update-address'))
      .toBe(true);

    await page.getByRole('button', { name: /Add by address/ }).click();
    const picker = page.getByTestId('add-by-address');
    await expect(picker.getByText('Cloud accounts')).toBeVisible();
    await expect(picker.getByText('Helpers')).toBeVisible();
    await picker.getByRole('button', { name: 'Switch as another device' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel('Switch')).toBeVisible();
    await dialog.getByLabel('Switch').selectOption('switch.kettle');
    await settled(page, dialog);
    await expectFits(page, dialog);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'devices-add-helper');
    clean(run);
  });
});
