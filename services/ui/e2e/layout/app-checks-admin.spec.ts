import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * Admin > Users & Devices > App checks on every form factor: the abuse.ch key
 * (a secret the server only ever answers "***" for) and the Common Sense switch.
 * The key used here is made up.
 */
const FAKE_KEY = 'test-key-not-real-0000';

async function open(page: Page, saved: string) {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockSettingsPages(page);
  const store: Record<string, string> = { malwarebazaar_auth_key: saved, app_vetting_common_sense_enabled: 'true' };
  const sent: Array<{ key: string; value: string }> = [];
  await page.route(/\/api\/settings(\/[a-z_]+)?$/, async (route) => {
    const req = route.request();
    const key = new URL(req.url()).pathname.split('/')[3];
    if (req.method() === 'PATCH' && key) {
      const value = (req.postDataJSON() as { value: string }).value;
      sent.push({ key, value });
      store[key] = value;
      return route.fulfill({ json: { key, value: key === 'malwarebazaar_auth_key' && value ? '***' : value } });
    }
    if (req.method() !== 'GET' || key) return route.fallback();
    const rest = await (await route.fetch()).json().catch(() => []) as Array<{ key: string }>;
    const ours = Object.entries(store).map(([k, v]) => ({ key: k, value: k === 'malwarebazaar_auth_key' && v ? '***' : v, description: '' }));
    return route.fulfill({ json: [...rest.filter((s) => !(s.key in store)), ...ours] });
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/admin/users');
  return { sent, errors };
}

test.describe('Admin app checks on every form factor', () => {
  test('save the abuse.ch key without ever showing it, and switch Common Sense off', async ({ page }, info) => {
    const run = await open(page, '');
    const panel = page.getByTestId('app-checks-panel');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByTestId('abuse-ch-key-state')).toHaveText('Not set up');
    await expect(panel.getByTestId('abuse-ch-key-save')).toBeDisabled();
    await settle(page);
    expect(await screenFindings(page, panel, 'app-checks', [])).toEqual([]);
    await panel.screenshot({ path: info.outputPath('app-checks-not-set-up.png') });

    const input = panel.getByTestId('abuse-ch-key');
    await expect(input).toHaveAttribute('type', 'password');
    await input.fill(FAKE_KEY);
    await panel.getByTestId('abuse-ch-key-save').click();
    await expect.poll(() => run.sent.find((s) => s.key === 'malwarebazaar_auth_key')?.value).toBe(FAKE_KEY);
    await expect(panel.getByTestId('abuse-ch-key-state')).toHaveText('Key saved');
    await expect(input).toHaveValue('');
    // The page never holds the key again: only the masked answer comes back.
    expect(await page.content()).not.toContain(FAKE_KEY);

    const sw = panel.getByTestId('common-sense-switch');
    await expect(sw).toHaveAttribute('aria-checked', 'true');
    await sw.click();
    await expect.poll(() => run.sent.find((s) => s.key === 'app_vetting_common_sense_enabled')?.value).toBe('false');
    await expect(sw).toHaveAttribute('aria-checked', 'false');

    await panel.scrollIntoViewIfNeeded();
    await settle(page);
    expect(await screenFindings(page, panel, 'app-checks-saved', [])).toEqual([]);
    await panel.screenshot({ path: info.outputPath('app-checks-saved.png') });
    await shot(page, info, 'app-checks');
    expect(run.errors).toEqual([]);
  });

  test('remove a saved key', async ({ page }) => {
    const run = await open(page, 'already-saved');
    const panel = page.getByTestId('app-checks-panel');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByTestId('abuse-ch-key-state')).toHaveText('Key saved');
    await panel.getByTestId('abuse-ch-key-remove').click();
    await expect.poll(() => run.sent.find((s) => s.key === 'malwarebazaar_auth_key')?.value).toBe('');
    await expect(panel.getByTestId('abuse-ch-key-state')).toHaveText('Not set up');
    expect(run.errors).toEqual([]);
  });
});
