import { test, expect as baseExpect } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

/**
 * "Jarvis is updating": while the deploy's flag is up a line under the top bar says so, on
 * every form factor; when an update that changed the web app is done, a browser is offered a
 * reload it can dismiss. Hermetic: the flag is answered here.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

test('the deploy banner says Jarvis is updating, then offers a reload, and fits every screen', async ({ page }, info) => {
  test.setTimeout(60_000);
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  let status = { updating: true, at: Math.floor(Date.now() / 1000), app: true };
  await page.route('**/api/deploy-status', (route) => route.fulfill({ json: status }));

  await page.goto('/devices');
  await expect(page.getByTestId('page-header')).toBeVisible();
  const banner = page.getByTestId('deploy-banner');
  await expect(banner).toContainText('Jarvis is updating');
  const width = page.viewportSize()?.width ?? 0;
  const box = await banner.boundingBox();
  expect(box, 'the banner is drawn').not.toBeNull();
  expect((box?.x ?? 0) + (box?.width ?? 0), 'the banner stays on screen').toBeLessThanOrEqual(width + 1);
  await expectNoHorizontalOverflow(page);
  await snap(page, info, 'deploy-updating');

  // The deploy is done and it changed the web app: offer a reload (the flag is polled every 3 s while updating).
  status = { updating: false, at: Math.floor(Date.now() / 1000), app: true };
  await expect(banner).toContainText('Jarvis was updated', { timeout: 10_000 });
  await expectTapTargets(banner);
  await expect(banner.getByRole('button', { name: 'Reload' })).toBeInViewport();
  await expectNoHorizontalOverflow(page);
  await snap(page, info, 'deploy-updated');
  await banner.getByRole('button', { name: 'Dismiss' }).click();
  await expect(banner).toBeHidden();

  expect(errors).toEqual([]);
  expect(mocks.unmocked).toEqual([]);
});

test('a flag a dead deploy left behind shows nothing', async ({ page }) => {
  await mockHome(page, 'admin');
  await page.route('**/api/deploy-status', (route) => route.fulfill({
    json: { updating: true, at: Math.floor(Date.now() / 1000) - 3600, app: false } }));
  const read = page.waitForResponse('**/api/deploy-status');
  await page.goto('/devices');
  await expect(page.getByTestId('page-header')).toBeVisible();
  await read;
  await expect(page.getByTestId('deploy-banner')).toHaveCount(0);
});
