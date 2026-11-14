import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Browser notices keep their buttons (public/push-sw.js): the page hands the signed-in person's key to
 * the push worker it registered, so a tapped button answers POST /api/push/action as them, and signing
 * out takes the key back. Run in every hermetic project. Headless Chromium grants no notification
 * permission, so showing a notice and tapping its buttons is covered by src/test/pushServiceWorker.test.ts,
 * which runs the same worker file. Every call the page makes is answered by fixtures/homeMocks.ts.
 */

const storedKey = (page: Page) => page.evaluate(async () => {
  const cache = await caches.open('jarvis-push-auth');
  const hit = await cache.match('/__jarvis_push_key');
  return hit ? hit.text() : null;
});

test.describe('browser notices: buttons', () => {
  test.describe.configure({ timeout: 90_000 });

  test('the push worker gets the key and loses it on sign-out',
    async ({ page }) => {
      const mocks = await mockHome(page, 'admin');
      const errors: string[] = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.goto('/automations');
      await expect(page.getByTestId('page-header')).toBeVisible();

      // Push turned on in this browser earlier (Settings), so the worker is registered...
      await page.evaluate(async () => {
        await navigator.serviceWorker.register('/push-sw.js');
        await navigator.serviceWorker.ready;
      });
      // ...and the next visit hands it the key.
      await page.reload();
      await expect.poll(() => storedKey(page)).toBe('test-key');

      await page.getByRole('button', { name: 'Account menu' }).first().click();
      await page.getByTestId('profile-menu-logout').click();
      await page.waitForURL('**/login');
      await expect.poll(() => storedKey(page)).toBe(null);
      expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
      expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
    });
});
