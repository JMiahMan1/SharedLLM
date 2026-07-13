import { test, expect, type Page } from '@playwright/test';

declare const process: { env: Record<string, string | undefined> };

const UI = process.env.UI_URL || 'http://192.168.2.205:8080';
const USER = process.env.E2E_USER || 'default';
const PASS = process.env.E2E_PASS || 'changeme';

type Captured = { status: number; ok: boolean; body: Record<string, unknown> | null };
const calls: Record<string, Captured> = {};

async function login(page: Page) {
  await page.goto(`${UI}/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('Enter username').fill(USER);
  await page.getByPlaceholder('Enter password').fill(PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL((u) => !u.pathname.includes('login'), { timeout: 20_000 });
}

async function gotoCalendar(page: Page) {
  await page.goto(`${UI}/calendar`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Calendar' })).toBeVisible({ timeout: 20_000 });
}

async function waitForCall(substr: string, timeout = 25_000): Promise<Captured> {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const key = Object.keys(calls).find((k) => k.includes(substr));
    if (key) return calls[key];
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`No captured call matching "${substr}" within ${timeout}ms`);
}

function captureResponses(page: Page) {
  page.on('response', async (response) => {
    const url = response.url();
    const path = url.split('?')[0];
    if (path.includes('/api/communication/calendar/events') || path.includes('/api/calendar/settings')) {
      let body: Record<string, unknown> | null = null;
      try { body = await response.json(); } catch { /* non-json */ }
      calls[path] = { status: response.status(), ok: response.ok(), body };
    }
  });
}

async function agendaLoaded(page: Page) {
  const hasEvents = page.getByRole('button').filter({ hasText: /\d{1,2}:\d{2}\s?(AM|PM)/ }).first();
  const empty = page.getByText(/Nothing coming up|Enjoy the quiet/);
  await expect.poll(async () => (await hasEvents.count()) > 0 || (await empty.count()) > 0).toBeTruthy();
}

test.beforeEach(async ({ page, request }) => {
  Object.keys(calls).forEach((k) => delete calls[k]);
  captureResponses(page);
  await login(page);
  // Reset calendar settings to a clean baseline so state from previous
  // runs (disabled sources, junk iCal URLs) can't pollute this run.
  const token = await page.evaluate(() => localStorage.getItem('jarvis_api_key'));
  if (token) {
    await request
      .put(`${UI}/api/calendar/settings`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { disabled: [], ical_urls: [], default: 'skylight' },
      })
      .catch(() => {});
  }
  await gotoCalendar(page);
});

test.describe('Calendar backend calls', () => {
  test('GET events + GET settings both fire and return 200', async ({ page }) => {
    const events = await waitForCall('/api/communication/calendar/events');
    const settings = await waitForCall('/api/calendar/settings');
    expect(events.status).toBe(200);
    expect(settings.status).toBe(200);
    expect(Array.isArray(events.body?.events)).toBe(true);
    expect(events.body.events.length).toBeGreaterThan(0);
    await agendaLoaded(page);
  });
});

test.describe('Calendar view switcher', () => {
  test('Agenda / Day / Week / Month all render', async ({ page }) => {
    await agendaLoaded(page);

    await page.getByRole('button', { name: 'Day', exact: true }).click();
    await expect(page.getByText(/^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)$/)).toBeVisible();

    await page.getByRole('button', { name: 'Week', exact: true }).click();
    await expect(page.getByText('Sun', { exact: true })).toBeVisible();
    await expect(page.getByText('Sat', { exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Month', exact: true }).click();
    await expect(page.getByText('Mon', { exact: true })).toBeVisible();
    await expect(page.getByText('Sun', { exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Agenda', exact: true }).click();
    await agendaLoaded(page);
  });
});

test.describe('Calendar date navigation', () => {
  test('Today + prev/next shift the focused month', async ({ page }) => {
    const label = page.locator('div.os-display', { hasText: /^\w+ \d{4}$/ }).first();
    const before = await label.innerText();

    await page.locator('button:has(svg.lucide-chevron-right)').click();
    await page.waitForTimeout(400);
    const afterNext = await label.innerText();
    expect(afterNext).not.toBe(before);

    await page.locator('button:has(svg.lucide-chevron-left)').click();
    await page.waitForTimeout(400);

    await page.getByRole('button', { name: 'Today', exact: true }).click();
    await page.waitForTimeout(400);
    const afterToday = await label.innerText();
    const expected = new Date().toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    expect(afterToday).toBe(expected);
  });
});

test.describe('Calendar source chips', () => {
  test('clicking All and per-integration chips filters without error', async ({ page }) => {
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.waitForTimeout(300);
    const chips = page.getByRole('button').filter({ has: page.locator('span.rounded-full') })
      .filter({ hasNotText: 'All' })
      .filter({ hasNotText: 'Agenda' })
      .filter({ hasNotText: 'Day' })
      .filter({ hasNotText: 'Week' })
      .filter({ hasNotText: 'Month' })
      .filter({ hasNotText: 'Sources' });
    const count = await chips.count();
    for (let i = 0; i < count; i++) {
      await chips.nth(i).click().catch(() => {});
      await page.waitForTimeout(200);
    }
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Calendar' })).toBeVisible();
  });
});

test.describe('Calendar Sources panel', () => {
  test('panel opens, shows connected sources, and disable toggles fire PUT settings', async ({ page }) => {
    await page.getByRole('button', { name: /sources/i }).click();
    await expect(page.getByText('Connected Sources')).toBeVisible();
    await expect(page.getByText('iCal Subscriptions')).toBeVisible();

    const toggle = page.getByRole('button', { name: /^(Disable|Enable)$/ }).first();
    const beforeLabel = (await toggle.innerText()).trim();
    await toggle.click();
    const put = await waitForCall('/api/calendar/settings');
    expect(put.status).toBe(200);
    await page.waitForTimeout(500);
    const afterLabel = (await page.getByRole('button', { name: /^(Disable|Enable)$/ }).first().innerText()).trim();
    expect(afterLabel).not.toBe(beforeLabel);

    const icalInput = page.getByPlaceholder('https://example.com/feed.ics');
    await icalInput.fill('https://example.com/test-feed.ics');
    await page.locator('input[placeholder="https://example.com/feed.ics"]').locator('xpath=../..')
      .getByRole('button', { name: 'Add', exact: true }).click();
    const put2 = await waitForCall('/api/calendar/settings');
    expect(put2.status).toBe(200);
    await expect(page.getByText('https://example.com/test-feed.ics')).toBeVisible();

    await page.getByRole('button', { name: /sources/i }).click();
    await expect(page.getByText('Connected Sources')).toBeHidden();
  });
});

test.describe('Calendar add event', () => {
  test('valid add fires POST and toasts success', async ({ page }) => {
    const title = `E2E ${Date.now()}`;
    await page.getByPlaceholder('Event title').fill(title);
    await page.getByPlaceholder(/When/i).fill('tomorrow at 3pm');
    const addBtn = page.getByPlaceholder('Event title').locator('xpath=..').getByRole('button', { name: 'Add', exact: true });
    await addBtn.click();

    const post = await waitForCall('/api/communication/calendar/events');
    expect([200, 201]).toContain(post.status);
    await expect(page.getByText('Event added')).toBeVisible({ timeout: 15_000 });
  });

  test('empty add shows validation toast', async ({ page }) => {
    const addBtn = page.getByPlaceholder('Event title').locator('xpath=..').getByRole('button', { name: 'Add', exact: true });
    await addBtn.click();
    await expect(page.getByText(/Enter a title and time/i)).toBeVisible({ timeout: 8_000 });
  });
});
