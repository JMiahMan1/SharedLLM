import { test } from '@playwright/test';
import type { WebSocket } from '@playwright/test';

const UI_URL = 'https://jarvis.sumemail.com';
const TEST_USER = 'testuser';
const TEST_PASS = 'changeme';

test('click sequence debug', async ({ page }) => {
  const wsEvents: string[] = [];
  const errors: string[] = [];
  const degradedStatuses: string[] = [];
  
  // Listen for WebSocket connections
  page.on('websocket', (ws: WebSocket) => {
    wsEvents.push(`CONNECT: ${ws.url()}`);
    ws.on('close', (event: { code: number }) => {
      wsEvents.push(`CLOSE: ${ws.url()} code=${event.code}`);
    });
    ws.on('framereceived', (frame: { error?: string }) => {
      if (frame.error) {
        wsEvents.push(`ERROR: ${ws.url()} ${frame.error}`);
      }
    });
  });
  
  // Listen for console messages
  page.on('console', msg => {
    if (msg.text().includes('degraded') || msg.text().includes('Reconnecting') || msg.text().includes('error')) {
      console.log(`[CONSOLE] ${msg.text()}`);
    }
  });
  
  // Listen for network errors
  page.on('response', async response => {
    if (response.status() >= 400) {
      errors.push(`${response.status()} ${response.url()}`);
    }
  });
  
  // Login
  await page.goto(`${UI_URL}/login`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByPlaceholder('Enter username').fill(TEST_USER);
  await page.getByPlaceholder('Enter password').fill(TEST_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
  
  // Click through pages
  const clickSequence = [
    ['Dashboard', 1000],
    ['Chat', 1000],
    ['Media', 1000],
    ['Workspaces', 1000],
    ['Dashboard', 3000]
  ];
  
  for (const [name, waitMs] of clickSequence) {
    console.log(`[TEST] Clicking ${name}...`);
    const btn = page.getByRole('button', { name, exact: true }).first()
      .or(page.getByText(name).first());
    if (await btn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await btn.click();
      await page.waitForTimeout(waitMs);
      
      // Check for degraded status after each click
      const degradedText = await page.locator('text=degraded').allTextContents();
      if (degradedText.length > 0) {
        degradedStatuses.push(`${name}: ${degradedText.join(', ')}`);
        console.log(`[TEST] DEGRADED FOUND after ${name}: ${degradedText.join(', ')}`);
      }
    }
  }
  
  // Take screenshot
  await page.screenshot({ path: '/tmp/click-sequence-debug.png', fullPage: true });
  
  // Check service statuses
  const serviceStatuses = await page.locator('[class*="status"], [class*="health"], [class*="ready"]').allTextContents();
  console.log('[TEST] Service statuses:', serviceStatuses.filter(s => s && s.trim().length > 0).slice(0, 20));
  
  // Print summary
  console.log('\n=== SUMMARY ===');
  console.log(`WebSocket events: ${wsEvents.length}`);
  if (wsEvents.length > 0) {
    console.log('Last 10 WS events:', wsEvents.slice(-10));
  }
  console.log(`Network errors: ${errors.length}`);
  if (errors.length > 0) {
    console.log('Network errors:', errors.slice(0, 20));
  }
  console.log(`Degraded statuses: ${degradedStatuses.length}`);
  if (degradedStatuses.length > 0) {
    console.log('Degraded details:', degradedStatuses);
  }
  console.log('Current URL:', page.url());
});
