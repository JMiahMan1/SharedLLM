import { test } from '@playwright/test';

const UI_URL = 'https://jarvis.sumemail.com';
const TEST_USER = 'testuser';
const TEST_PASS = 'changeme';

test('click sequence and screenshot', async ({ page }) => {
  // Login
  await page.goto(`${UI_URL}/login`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByPlaceholder('Enter username').fill(TEST_USER);
  await page.getByPlaceholder('Enter password').fill(TEST_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
  
  // Click Dashboard
  console.log('[TEST] Clicking Dashboard...');
  const dashboardBtn = page.getByRole('button', { name: 'Dashboard', exact: true }).first()
    .or(page.getByText('Dashboard').first());
  if (await dashboardBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await dashboardBtn.click();
    await page.waitForTimeout(1000);
  }
  
  // Click Chat
  console.log('[TEST] Clicking Chat...');
  const chatBtn = page.getByRole('button', { name: 'Chat', exact: true }).first()
    .or(page.getByText('Chat').first());
  if (await chatBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await chatBtn.click();
    await page.waitForTimeout(1000);
  }
  
  // Click Media
  console.log('[TEST] Clicking Media...');
  const mediaBtn = page.getByRole('button', { name: 'Media', exact: true }).first()
    .or(page.getByText('Media').first());
  if (await mediaBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await mediaBtn.click();
    await page.waitForTimeout(1000);
  }
  
  // Click Workspaces
  console.log('[TEST] Clicking Workspaces...');
  const workspacesBtn = page.getByRole('button', { name: 'Workspaces', exact: true }).first()
    .or(page.getByText('Workspaces').first());
  if (await workspacesBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await workspacesBtn.click();
    await page.waitForTimeout(1000);
  }
  
  // Go back to Dashboard
  console.log('[TEST] Clicking Dashboard again...');
  if (await dashboardBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await dashboardBtn.click();
    console.log('[TEST] Waiting 3 seconds on Dashboard...');
    await page.waitForTimeout(3000);
  }
  
  // Take screenshot
  await page.screenshot({ path: '/tmp/sequence-screenshot.png', fullPage: true });
  console.log('[TEST] Screenshot saved to /tmp/sequence-screenshot.png');
  
  // Check for reconnecting
  const reconnecting = page.locator('text=Reconnecting to Jarvis server').first();
  if (await reconnecting.isVisible({ timeout: 2000 }).catch(() => false)) {
    console.log('[TEST] RECONNECTING MESSAGE VISIBLE');
  } else {
    console.log('[TEST] RECONNECTING MESSAGE NOT FOUND');
  }
  
  // Check for degraded
  const degraded = page.locator('text=degraded').first();
  if (await degraded.isVisible({ timeout: 2000 }).catch(() => false)) {
    console.log('[TEST] DEGRADED STATUS DETECTED');
  } else {
    console.log('[TEST] DEGRADED STATUS NOT FOUND');
  }
  
  console.log('[TEST] Current URL:', page.url());
});
