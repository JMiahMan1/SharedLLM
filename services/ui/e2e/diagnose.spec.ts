import { test } from '@playwright/test';

const UI_URL = 'http://192.168.2.205:8080';

test('diagnose login v3', async ({ page }) => {
  await page.goto(`${UI_URL}/login`, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForSelector('form', { timeout: 5000 });
  await page.waitForTimeout(500);
  
  await page.fill('input[placeholder="Enter username"]', 'default');
  await page.fill('input[placeholder="Enter password"]', 'changeme');
  await page.click('button[type="submit"]');
  
  try {
    await page.waitForURL('**/dashboard', { timeout: 10000 });
    console.log('[v3] Dashboard ✓');
  } catch {
    console.log(`[v3] Still on: ${page.url()}`);
  }
  
  await page.waitForTimeout(2000);
  
  const token = await page.evaluate(() => localStorage.getItem('jarvis_api_key'));
  console.log(`[v3] Token: ${token ? 'yes' : 'no'}`);
  
  // Try pushState to /media
  console.log('[v3] pushState to /media...');
  await page.evaluate(() => {
    window.history.pushState({}, '', '/media');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await page.waitForTimeout(5000);
  
  console.log(`[v3] URL: ${page.url()}`);
  
  const mediaHeading = await page.getByRole('heading', { name: 'Media', level: 1 })
    .isVisible({ timeout: 5000 }).catch(() => false);
  console.log(`[v3] Media heading: ${mediaHeading}`);
  
  if (!mediaHeading) {
    const isLogin = await page.locator('input[type="password"]').isVisible({ timeout: 2000 }).catch(() => false);
    console.log(`[v3] Redirected to login: ${isLogin}`);
    await page.screenshot({ path: 'test-results/diagnose-v3.png' });
  }
});
