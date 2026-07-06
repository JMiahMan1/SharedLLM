import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';

function loadCredentials(): { user: string; pass: string } {
  const envTestPath = path.resolve(__dirname, '../../../.env.test');
  if (fs.existsSync(envTestPath)) {
    const envContent = fs.readFileSync(envTestPath, 'utf-8');
    for (const line of envContent.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const eqIdx = trimmed.indexOf('=');
      if (eqIdx > 0) {
        process.env[trimmed.slice(0, eqIdx).trim()] = trimmed.slice(eqIdx + 1).trim();
      }
    }
  }
  const user = process.env.TEST_USER;
  const pass = process.env.TEST_PASS;
  if (!user || !pass) throw new Error('Credentials not found in .env.test');
  return { user, pass };
}
const { user: LOGIN_USER, pass: LOGIN_PASS } = loadCredentials();

async function loginAsDefault(page: Page): Promise<void> {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').waitFor({ state: 'visible', timeout: 10000 });
  const useDifferent = page.locator('button:text("Use different account")').first();
  if (await useDifferent.isVisible({ timeout: 2000 }).catch(() => false)) {
    await useDifferent.click();
    await page.waitForTimeout(500);
  }
  await page.getByPlaceholder('Enter username').fill(LOGIN_USER);
  await page.getByPlaceholder('Enter password').fill(LOGIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForFunction(() => {
    const root = document.getElementById('root');
    if (!root) return false;
    const text = (root.innerText || '').trim();
    return text.length > 0 && root.querySelectorAll('button, a, [role="button"], input').length > 0;
  }, { timeout: 15000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

test.describe('Dashboard Deep Diagnostic', () => {
  test('capture dashboard rendering state', async ({ page }) => {
    test.setTimeout(90000);
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3500);

    console.log('CURRENT URL:', page.url());

    // Screenshot FIRST
    await page.screenshot({ path: '/tmp/dashboard-full.png', fullPage: true }).catch((e) => console.log('screenshot err', e));
    console.log('Screenshot saved');

    const title = await page.title();
    console.log('PAGE TITLE:', title);

    // Sidebar active link
    const activeNav = await page.locator('aside nav a[class*="bg-purple"], aside nav a[aria-current="page"]').first().innerText().catch(() => '(none)');
    console.log('ACTIVE NAV:', activeNav);

    // Widget panels - dump full text of each
    const glassPanels = await page.locator('.glass-panel').count();
    console.log('GLASS PANELS COUNT:', glassPanels);
    for (let i = 0; i < glassPanels; i++) {
      const txt = (await page.locator('.glass-panel').nth(i).innerText().catch(() => '(err)')).replace(/\n+/g, ' | ').slice(0, 160);
      console.log(`  Panel ${i}: ${txt}`);
    }

    // Health status indicator - look for the sidebar status or banner
    const statusText = await page.locator('text=/Nominal|Degraded|Offline|All Services/i').allInnerTexts().catch(() => []);
    console.log('STATUS TEXT MATCHES:', JSON.stringify(statusText));

    // Any error/warning banners
    const banner = await page.locator('.glass-card, .neon-border').first().innerText().catch(() => '(none)');
    console.log('FIRST GLASS CARD:', banner.slice(0, 200));
  });
});
