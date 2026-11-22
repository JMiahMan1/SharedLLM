import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';
import { expectNoHorizontalOverflow, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Jarvis's local AI (the GPU computer) is off. On every form factor a status line under the top bar
 * says so and what still works, and a question asked in the Quick Assistant is answered with the
 * gateway's plain sentence (a 503 carrying it), not a blank bubble or "Failed to send". Hermetic:
 * every call is answered here, with fake names only.
 */

const OFFLINE =
  "Jarvis's thinking brain (the local AI) is offline right now, so I can still control the house and tell you " +
  "the time and the weather, but I can't answer that yet. I'll let you know when it's back.";

async function offlineBoard(page: Page) {
  const errors: string[] = [];
  const asked: unknown[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await seedAuth(page);
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/'), (route: Route) => {
    const req = route.request();
    if (req.method() !== 'GET') return route.fulfill({ json: { status: 'SUCCESS' } });
    return route.fulfill({ json: [] });
  });
  await page.route('**/api/users/me', (route) => route.fulfill({ json: {
    id: 1, username: 'parent', display_name: 'Parent', is_admin: true, role: 'admin' } }));
  await page.route('**/api/widgets/settings', (route) => route.fulfill({ json: {
    widgets: [{ widget_key: 'quick_assistant', visibility: 'visible', order_index: 0, size: 'medium', is_pinned: false,
      sort_mode: null, pinned_devices: [], config: {}, updated_at: 0 }],
    quick_assistant_enabled: true,
  } }));
  await page.route((u: URL) => u.pathname === '/api/status/local-ai', (route) => route.fulfill({ json: {
    local_ai: { state: 'down', offline: true, since: 1_795_000_000, message: OFFLINE,
      still_working: ['home control', 'time and date', 'weather'] },
    backends: { local_ai: { state: 'down' }, arcade: { state: 'down' } },
    any_offline: true,
  } }));
  await page.route((u: URL) => u.pathname === '/api/chat', (route) => {
    asked.push(route.request().postDataJSON());
    return route.fulfill({ status: 503, headers: { 'Retry-After': '15' }, json: {
      model: 'assistant', done: true, status: 'ERROR', message: { role: 'assistant', content: OFFLINE }, error: OFFLINE,
      error_code: 'local_ai_offline', error_id: 'abcd1234', offline: 'local_ai', retryable: true,
    } });
  });
  await page.goto('/');
  return { errors, asked };
}

test('the offline banner and a plain offline reply in chat, on every screen', async ({ page }, info) => {
  test.setTimeout(90_000);
  const run = await offlineBoard(page);

  const banner = page.getByTestId('local-ai-banner');
  await expect(banner).toContainText("Jarvis's thinking brain is offline right now");
  await expect(banner).toContainText('Home control, the time and the weather still work');
  await expect(banner).toContainText('Games is offline too');
  await expect(banner).toBeInViewport();
  const width = page.viewportSize()?.width ?? 0;
  const box = await banner.boundingBox();
  expect((box?.x ?? 0) + (box?.width ?? 0), 'the banner stays on screen').toBeLessThanOrEqual(width + 1);

  const widget = page.locator('[data-widget="quick_assistant"]');
  await widget.scrollIntoViewIfNeeded();
  const input = widget.getByRole('textbox', { name: 'Message the assistant' });
  await input.fill('What does the parable of the sower mean?');
  await input.press('Enter');

  const reply = widget.getByTestId('assistant-notice');
  await expect(reply).toContainText("thinking brain (the local AI) is offline");
  await expect(widget.getByText('What does the parable of the sower mean?')).toBeVisible();
  await expect(page.getByText('Failed to send message')).toHaveCount(0);
  await expect(page.getByText(/Reconnecting/)).toHaveCount(0); // the 503 says it all: no retry toasts
  expect(await page.locator('body').innerText()).not.toMatch(/11434|Cannot connect|Connect call failed/);
  expect(run.asked).toHaveLength(1);

  await expectNoHorizontalOverflow(page);
  await snap(page, info, 'local-ai-offline');
  expect(run.errors).toEqual([]);
});

test('no banner while the local AI is up', async ({ page }) => {
  await seedAuth(page);
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/'), (route: Route) =>
    route.fulfill({ json: route.request().method() === 'GET' ? [] : { status: 'SUCCESS' } }));
  await page.route('**/api/users/me', (route) => route.fulfill({ json: {
    id: 1, username: 'parent', display_name: 'Parent', is_admin: true, role: 'admin' } }));
  const read = page.waitForResponse((r) => r.url().includes('/api/status/local-ai'));
  await page.route((u: URL) => u.pathname === '/api/status/local-ai', (route) => route.fulfill({ json: {
    local_ai: { state: 'up', offline: false, since: null, message: null, still_working: [] }, backends: {}, any_offline: false,
  } }));
  await page.goto('/');
  await read;
  await expect(page.getByTestId('local-ai-banner')).toHaveCount(0);
});
