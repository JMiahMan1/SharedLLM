import { test, expect } from '@playwright/test';

/**
 * Live frontend verification for the Raven end-to-end flow.
 *
 * Companion to the Python test in tests/integration/test_raven_user_cases.py.
 * It verifies the UI surfaces Raven/agent work touches: the Workspaces page
 * (where repos are managed) and the Integrations page (GitHub / Nextcloud etc.).
 *
 * DISABLED IN CI: this whole spec skips unless LIVE_E2E=1 is set, because it
 * requires the live UI stack. Run manually with:
 *   LIVE_E2E=1 npx playwright test raven-live.spec.ts
 */
const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_USER = process.env.E2E_USER || 'default';
const ADMIN_PASS = process.env.E2E_PASS || 'admin';

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

test.describe('Raven live frontend checks', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!process.env.LIVE_E2E, 'LIVE_E2E not set — skipping live UI checks');
    await loginAsAdmin(page);
  });

  test('Workspaces page loads and exposes repository management', async ({ page }) => {
    await page.goto(`${UI_URL}/workspaces`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    await expect(page.getByRole('button', { name: /add repository/i })).toBeVisible({ timeout: 10000 });
  });

  test('Integrations page shows GitHub / Nextcloud integration surface', async ({ page }) => {
    await page.goto(`${UI_URL}/admin/integrations`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    await expect(page.getByText(/Personal Integrations/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/GitHub/i)).toBeVisible({ timeout: 10000 });
  });
});
