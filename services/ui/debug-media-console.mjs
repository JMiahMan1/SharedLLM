import { chromium } from '@playwright/test';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  
  // Set up console and error event listeners
  page.on('console', msg => {
    console.log(`[PAGE CONSOLE] [${msg.type()}] ${msg.text()}`);
  });
  page.on('pageerror', err => {
    console.log('[PAGE ERROR]', err.message);
  });
  page.on('requestfailed', req => {
    console.log(`[REQUEST FAILED] ${req.method()} ${req.url()} - ${req.failure()?.errorText || 'unknown error'}`);
  });
  page.on('response', resp => {
    if (resp.status() >= 400) {
      console.log(`[HTTP ERROR] ${resp.request().method()} ${resp.url()} -> status ${resp.status()}`);
    }
  });

  console.log('Navigating to login...');
  await page.goto('http://192.168.2.205:8080/login');
  
  console.log('Logging in...');
  await page.getByPlaceholder('Enter username').fill('default');
  await page.getByPlaceholder('Enter password').fill('admin');
  await page.getByRole('button', { name: /sign in/i }).click();
  
  console.log('Waiting for dashboard...');
  await page.waitForURL('**/dashboard', { timeout: 10000 });
  await page.waitForTimeout(2000);
  
  console.log('Navigating to media...');
  await page.goto('http://192.168.2.205:8080/media');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);

  console.log('Locating play buttons...');
  
  // Find play buttons inside the page and click one associated with MA
  // Quick resume item structure usually has a Play button inside.
  // Let's try to click one with MA/music
  const maItem = page.locator('button:has-text("Play"), button[aria-label*="play" i]').first();
  if (await maItem.isVisible()) {
    console.log('Clicking the play button...');
    await maItem.click();
    await page.waitForTimeout(1000);
  } else {
    console.log('No play button found.');
  }

  // Wait 10 seconds to let the audio tag load/error
  console.log('Waiting 10 seconds for playback...');
  await page.waitForTimeout(10000);

  await page.screenshot({ path: '/tmp/media-debug-play.png', fullPage: true });
  console.log('Screenshot saved.');

  await browser.close();
  process.exit(0);
})();
