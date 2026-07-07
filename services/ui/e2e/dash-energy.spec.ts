import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';

function loadCredentials(): { user: string; pass: string } {
  const p = path.resolve(__dirname, '../../../.env.test');
  if (fs.existsSync(p)) for (const l of fs.readFileSync(p, 'utf-8').split('\n')) {
    const t = l.trim(); if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('='); if (i > 0) process.env[t.slice(0, i).trim()] = t.slice(i + 1).trim();
  }
  const u = process.env.TEST_USER, pw = process.env.TEST_PASS;
  if (!u || !pw) throw new Error('no creds'); return { user: u, pass: pw };
}
const { user: LOGIN_USER, pass: LOGIN_PASS } = loadCredentials();

async function login(page: Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').waitFor({ state: 'visible', timeout: 10000 });
  const u = page.locator('button:text("Use different account")').first();
  if (await u.isVisible({ timeout: 2000 }).catch(() => false)) { await u.click(); await page.waitForTimeout(500); }
  await page.getByPlaceholder('Enter username').fill(LOGIN_USER);
  await page.getByPlaceholder('Enter password').fill(LOGIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForFunction(() => {
    const r = document.getElementById('root');
    return r && (r.innerText || '').trim().length > 0 && r.querySelectorAll('button,a,[role=button],input').length > 0;
  }, { timeout: 15000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

test('Energy Insights widget shows enrolled telemetry', async ({ page }) => {
  test.setTimeout(90000);
  const errs: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', (e) => errs.push(`PAGEERR: ${e.message}`));
  page.on('response', async (r) => {
    if (r.url().includes('telemetry')) console.log('RESP', r.request().method(), r.url().split('/api/').pop(), '->', r.status());
  });
  await login(page);
  await page.goto(`${UI_URL}/`, { waitUntil: 'domcontentloaded' }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

  const widget = page.locator('.glass-panel', { hasText: 'Energy Insights' });
  await widget.waitFor({ state: 'visible', timeout: 20000 });
  // Poll for the widget to finish loading its telemetry data
  await widget.waitFor(async () => {
    const t = (await widget.innerText()).replace(/\n+/g, ' ');
    return /W/.test(t) || t.includes('Telemetry Service Unconfigured') || t.includes('No energy data');
  }, { timeout: 35000 }).catch(() => {});
  const txt = (await widget.innerText()).replace(/\n+/g, ' | ');
  console.log('ENERGY WIDGET TEXT:', txt);

  // Should NOT show the unconfigured/error state now
  expect(txt).not.toContain('Telemetry Service Unconfigured');
  // Should show metric values (W)
  expect(txt).toMatch(/W/);
  console.log('CONSOLE ERRORS:', errs.filter(e => e.includes('telemetry') || e.includes('500')).join(' || ') || '(none telemetry/500)');
});
