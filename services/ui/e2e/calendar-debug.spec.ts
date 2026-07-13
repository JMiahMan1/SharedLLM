import { test, type Page } from '@playwright/test';

declare const process: { env: Record<string, string | undefined> };
const UI = process.env.UI_URL || 'http://192.168.2.205:8080';
const USER = process.env.E2E_USER || 'default';
const PASS = process.env.E2E_PASS || 'changeme';

async function login(page: Page) {
  await page.goto(`${UI}/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('Enter username').fill(USER);
  await page.getByPlaceholder('Enter password').fill(PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.includes('login'), { timeout: 20_000 });
}

test('debug events payload', async ({ page }) => {
  page.on('response', async (resp) => {
    const u = resp.url();
    if (u.includes('/api/communication/calendar/events') || u.includes('/api/calendar/settings')) {
      let body: Record<string, unknown> | null = null;
      try {
        body = await resp.json() as Record<string, unknown>;
      } catch {
        // Ignored
      }
      console.log('RESP', resp.status(), u.split('?')[0], 'EVENTS=', body && Array.isArray(body.events) ? body.events.length : 'n/a', 'BODYKEYS=', body ? Object.keys(body) : null);
    }
  });
  await login(page);
  await page.goto(`${UI}/calendar`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(20000);
  // also fetch directly from page context
  const token = await page.evaluate(() => localStorage.getItem('jarvis_api_key'));
  const direct = await page.evaluate(async (tk) => {
    const r = await fetch('/api/communication/calendar/events', { headers: { Authorization: `Bearer ${tk}` } });
    const j = await r.json();
    const details = j.detail || {};
    return {
      status: r.status,
      events: Array.isArray(j.events) ? j.events.length : 'n/a',
      message: j.message,
      default: details.default,
      integrations: (details.integrations || []).map((i: { type: string; enabled: boolean }) => `${i.type}:${i.enabled ? 'on' : 'off'}`)
    };
  }, token);
  console.log('TOKEN_PREFIX', String(token).slice(0, 8));
  console.log('DIRECT', JSON.stringify(direct));
});
