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
    for (const line of fs.readFileSync(envTestPath, 'utf-8').split('\n')) {
      const t = line.trim();
      if (!t || t.startsWith('#')) continue;
      const i = t.indexOf('=');
      if (i > 0) process.env[t.slice(0, i).trim()] = t.slice(i + 1).trim();
    }
  }
  const user = process.env.TEST_USER, pass = process.env.TEST_PASS;
  if (!user || !pass) throw new Error('Credentials not found in .env.test');
  return { user, pass };
}
const { user: LOGIN_USER, pass: LOGIN_PASS } = loadCredentials();

async function loginAsDefault(page: Page): Promise<void> {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').waitFor({ state: 'visible', timeout: 10000 });
  const useDifferent = page.locator('button:text("Use different account")').first();
  if (await useDifferent.isVisible({ timeout: 2000 }).catch(() => false)) { await useDifferent.click(); await page.waitForTimeout(500); }
  await page.getByPlaceholder('Enter username').fill(LOGIN_USER);
  await page.getByPlaceholder('Enter password').fill(LOGIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForFunction(() => {
    const root = document.getElementById('root');
    return root && (root.innerText || '').trim().length > 0 && root.querySelectorAll('button,a,[role="button"],input').length > 0;
  }, { timeout: 15000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

test.describe('Dashboard Interaction Deep Test', () => {
  test('quick notes create + save flow', async ({ page }) => {
    test.setTimeout(90000);
    const consoleErrors: string[] = [];
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', (e) => consoleErrors.push(`PAGEERROR: ${e.message}`));

    await loginAsDefault(page);
    await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle' }).catch(() => {});
    await page.waitForTimeout(3500);

    // Find Quick Notes widget panel
    const quickNotes = page.locator('.glass-panel', { hasText: 'Quick Notes' });
    await expect(quickNotes).toBeVisible();

    // Look for a title/body input and Save button inside the widget
    const titleInput = quickNotes.getByPlaceholder(/title/i).first();
    const bodyInput = quickNotes.locator('textarea').first();
    const saveBtn = quickNotes.getByRole('button', { name: /save/i }).first();

    if (await titleInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      const ts = `E2E Test ${Date.now()}`;
      await titleInput.fill(ts);
      if (await bodyInput.isVisible({ timeout: 2000 }).catch(() => false)) {
        await bodyInput.fill('Created by automated deep test.');
      }
      await saveBtn.click();
      await page.waitForTimeout(2500);
      console.log('Quick Notes save clicked. Widget text now:', (await quickNotes.innerText()).replace(/\n+/g, ' | ').slice(0, 200));
    } else {
      console.log('Quick Notes: no title input visible — widget may render notes list only. Widget text:', (await quickNotes.innerText()).replace(/\n+/g, ' | ').slice(0, 200));
    }

    const notesText = await quickNotes.innerText();
    expect(notesText).not.toContain("Note error");
    console.log('CONSOLE ERRORS:', consoleErrors.join(' || ') || '(none)');
  });

  test('device controls toggle interaction', async ({ page }) => {
    test.setTimeout(90000);
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle' }).catch(() => {});
    await page.waitForTimeout(3500);

    const devicePanel = page.locator('.glass-panel', { hasText: 'Device Controls' });
    await expect(devicePanel).toBeVisible();

    // Find a device toggle (switch role)
    const toggle = devicePanel.getByRole('switch').first();
    if (await toggle.isVisible({ timeout: 3000 }).catch(() => false)) {
      const before = await toggle.getAttribute('aria-checked');
      await toggle.click();
      await page.waitForTimeout(2000);
      const after = await toggle.getAttribute('aria-checked');
      console.log(`Device toggle aria-checked: before=${before} after=${after}`);
      // revert
      if (before !== after) { await toggle.click(); await page.waitForTimeout(1500); }
    } else {
      console.log('No device switch found in Device Controls panel.');
    }
  });

  test('widget expand + gear menu', async ({ page }) => {
    test.setTimeout(90000);
    await loginAsDefault(page);
    await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle' }).catch(() => {});
    await page.waitForTimeout(3500);

    // Gear menu
    const gear = page.locator('button[title="Widget options"]').first();
    await gear.click();
    await page.waitForTimeout(500);
    const menu = page.locator('.fixed.z-50.glass-card').first();
    await expect(menu).toBeVisible();
    console.log('Gear menu text:', (await menu.innerText()).replace(/\n+/g, ' | ').slice(0, 150));
    // close
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
    await expect(menu).not.toBeVisible();

    // Expand first widget if expand button exists
    const expandBtn = page.locator('.glass-panel').first().getByRole('button', { name: /expand/i }).first();
    if (await expandBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await expandBtn.click();
      await page.waitForTimeout(1000);
      console.log('Expanded first widget.');
      await page.keyboard.press('Escape').catch(() => {});
    } else {
      console.log('No expand button on first widget (may not be expandable).');
    }
  });
});
