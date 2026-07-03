import { test, expect } from '@playwright/test';

const UI_URL = 'https://jarvis.sumemail.com';
const TEST_USER = 'testuser';
const TEST_PASS = 'changeme';

test('screenshot and check for reconnecting', async ({ page }) => {
  await page.goto(`${UI_URL}/login`, { waitUntil: 'networkidle', timeout: 30000 });
  
  await page.getByPlaceholder('Enter username').fill(TEST_USER);
  await page.getByPlaceholder('Enter password').fill(TEST_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(3000);
  
  // Take screenshot
  await page.screenshot({ path: '/tmp/ui-screenshot.png', fullPage: true });
  console.log('[TEST] Screenshot saved to /tmp/ui-screenshot.png');
  
  // Check for reconnecting message
  const reconnecting = page.locator('text=Reconnecting to Jarvis server').first();
  if (await reconnecting.isVisible({ timeout: 2000 }).catch(() => false)) {
    console.log('[TEST] RECONNECTING MESSAGE VISIBLE');
    await page.screenshot({ path: '/tmp/reconnecting-visible.png', fullPage: true });
  } else {
    console.log('[TEST] RECONNECTING MESSAGE NOT FOUND');
  }
  
  // Check for degraded status
  const degraded = page.locator('text=degraded').first();
  if (await degraded.isVisible({ timeout: 2000 }).catch(() => false)) {
    console.log('[TEST] DEGRADED STATUS DETECTED');
    await page.screenshot({ path: '/tmp/degraded-visible.png', fullPage: true });
  } else {
    console.log('[TEST] DEGRADED STATUS NOT FOUND');
  }
  
  // Check for notifications
  const notifications = page.locator('.notification, .toast, [role="alert"], .alert').first();
  if (await notifications.isVisible({ timeout: 2000 }).catch(() => false)) {
    console.log('[TEST] NOTIFICATION FOUND');
    console.log('[TEST] Notification text:', await notifications.textContent());
    await page.screenshot({ path: '/tmp/notification-visible.png', fullPage: true });
  } else {
    console.log('[TEST] NO NOTIFICATIONS FOUND');
  }
  
  // Get page title
  console.log('[TEST] Page title:', await page.title());
  console.log('[TEST] Page URL:', page.url());
});
