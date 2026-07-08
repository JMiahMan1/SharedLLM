import { test } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'https://jarvis.sumemail.com';
const TEST_USER = process.env.TEST_USER || 'testuser';
const TEST_PASS = process.env.TEST_PASS || 'changeme';

test.describe('Rapid Menu Clicking', () => {
  test('click through menu items rapidly', async ({ page }) => {
    console.log('[TEST] Navigating to UI...');
    await page.goto(`${UI_URL}/login`, { waitUntil: 'networkidle', timeout: 30000 });
    
    console.log('[TEST] Logging in...');
    await page.getByPlaceholder('Enter username').fill(TEST_USER);
    await page.getByPlaceholder('Enter password').fill(TEST_PASS);
    await page.getByRole('button', { name: /sign in/i }).click();
    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2000);
    
    console.log('[TEST] Starting rapid menu clicks...');
    const messages: string[] = [];
    const errors: string[] = [];
    
    // Listen for the reconnecting message
    page.on('console', msg => {
      if (msg.text().includes('Reconnecting') || msg.text().includes('degraded')) {
        messages.push(`RECONNECTING: ${msg.text()}`);
        console.log(`[TEST] RECONNECTING: ${msg.text()}`);
      }
    });
    
    // Listen for network errors
    page.on('response', async response => {
      if (response.status() >= 400) {
        errors.push(`${response.status()} ${response.url()}`);
      }
    });
    
    // Rapidly click through different menu items
    const menuItems = [
      'Dashboard',
      'Media Console',
      'Music',
      'Workspaces',
      'Knowledge Hub',
      'Identity',
      'Admin',
      'Communication'
    ];
    
    for (let i = 0; i < 5; i++) {
      console.log(`[TEST] Round ${i + 1}/5`);
      for (const item of menuItems) {
        const menuItem = page.getByRole('button', { name: item, exact: true }).first()
          .or(page.getByText(item).first());
        
        if (await menuItem.isVisible({ timeout: 1000 }).catch(() => false)) {
          await menuItem.click();
          await page.waitForTimeout(200); // Short delay to simulate rapid clicking
        }
      }
    }
    
    // Wait a bit to see if reconnecting message appears
    await page.waitForTimeout(3000);
    
    console.log(`[TEST] Test completed. Messages captured: ${messages.length}`);
    console.log(`[TEST] Network errors: ${errors.length}`);
    
    if (messages.length > 0) {
      console.log('[TEST] RECONNECTING MESSAGES:', messages);
    }
    if (errors.length > 0) {
      console.log('[TEST] NETWORK ERRORS:', errors.slice(0, 20));
    }
    
    // Check if any degradation message appears
    const degraded = page.locator('text=degraded').first();
    if (await degraded.isVisible({ timeout: 2000 }).catch(() => false)) {
      console.log('[TEST] DEGRADED STATUS DETECTED');
    }
    
    // Check for reconnecting message in the page
    const reconnecting = page.locator('text=Reconnecting to Jarvis server').first();
    if (await reconnecting.isVisible({ timeout: 2000 }).catch(() => false)) {
      console.log('[TEST] RECONNECTING MESSAGE VISIBLE');
    }
    
    // Check for "Reconnecting" or "degraded" in any element
    const bodyText = await page.body();
    if (bodyText && (bodyText.textContent?.includes('Reconnecting') || bodyText.textContent?.includes('degraded'))) {
      console.log('[TEST] RECONNECTING/DEGRADED FOUND IN PAGE');
      console.log('[TEST] Page content snippet:', bodyText.textContent?.substring(0, 500));
    }
  });
});
