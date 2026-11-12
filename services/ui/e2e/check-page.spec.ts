import { test } from '@playwright/test';

const UI_URL = process.env.UI_URL || '';
// Live only: never fall back to a real server. Without UI_URL every test here skips.
test.skip(!UI_URL, 'Live spec: set UI_URL to the server under test (for example http://192.0.2.205:8080).');
const TEST_USER = 'testuser';
const TEST_PASS = 'changeme';

test('@live check page state', async ({ page }) => {
  await page.goto(`${UI_URL}/login`, { waitUntil: 'networkidle', timeout: 30000 });
  
  await page.getByPlaceholder('Enter username').fill(TEST_USER);
  await page.getByPlaceholder('Enter password').fill(TEST_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(3000);
  
  // Get all text content
  const bodyText = await page.locator('body').textContent();
  console.log('[TEST] Page text content:');
  console.log(bodyText?.substring(0, 3000));
  
  // Check for any status indicators
  const statusElements = page.locator('[class*="status"], [class*="degraded"], [class*="reconnect"]').all();
  const statuses = await Promise.all((await statusElements).map(el => el.textContent()));
  console.log('[TEST] Status elements:', statuses.filter(s => s && s.trim().length > 0));
  
  // Check for WebSocket connections
  const wsConnections = page.context().routes().all() || [];
  console.log('[TEST] WebSocket connections:', wsConnections.length);
});
