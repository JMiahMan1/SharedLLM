import { test, expect as baseExpect, type Page, type TestInfo } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * Voices on every form factor: Settings > Voice (a person's own voice, with a
 * sample to hear first), the speech server in Admin > Raven Ops, and what a
 * voice satellite may control when it recognises nobody (Admin > Users).
 */

/** A tiny silent WAV, so "Hear it" has something real to play. */
const SILENT_WAV = Buffer.from(
  'UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=', 'base64',
).toString('base64');

async function open(page: Page, path: string) {
  await quietHotReload(page);
  const home = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockSettingsPages(page);
  const sent: Array<{ method: string; path: string; body: unknown }> = [];
  await page.route(/\/api\/(users\/me|tts\/sample|admin\/raven\/config|settings\/household_device_access)$/, async (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (req.method() === 'GET') return route.fallback();
    const body = req.postDataJSON();
    sent.push({ method: req.method(), path: p, body });
    if (p === '/api/tts/sample') return route.fulfill({ json: { audio_base64: SILENT_WAV, mime_type: 'audio/wav' } });
    if (p === '/api/users/me') {
      const voice = (body as { preferred_tts_voice?: string }).preferred_tts_voice ?? null;
      return route.fulfill({ json: { id: 1, username: 'parent', display_name: 'Parent', is_admin: true, preferred_tts_voice: voice } });
    }
    if (p.startsWith('/api/settings/')) return route.fulfill({ json: { key: 'household_device_access', value: (body as { value: string }).value } });
    return route.fulfill({ json: { status: 'SUCCESS' } });
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(path);
  return { home, sent, errors };
}

async function look(page: Page, info: TestInfo, testId: string, name: string) {
  const panel = page.getByTestId(testId);
  await panel.scrollIntoViewIfNeeded();
  await settle(page);
  const found = await screenFindings(page, panel, name, []);
  await panel.screenshot({ path: info.outputPath(`${name}.png`) });
  await shot(page, info, name);
  return found;
}

test.describe('Voices on every form factor', () => {
  test('Settings > Voice: choose your own voice, hear it first, or go back to Jarvis', async ({ page }, info) => {
    const run = await open(page, '/settings');
    const panel = page.getByTestId('my-voice-panel');
    await expect(panel).toBeVisible();
    const select = panel.getByTestId('my-voice-select');
    // Jarvis's own voice first, named; then readable names, grouped.
    await expect(select.locator('option').first()).toHaveText("Jarvis's voice");
    await expect(panel.getByTestId('my-voice-jarvis')).toHaveText("Jarvis's voice right now: HFC male, American (Piper)");
    await expect(select.locator('optgroup')).toHaveCount(4);
    await expect(select.locator('option', { hasText: 'Emma, British female' })).toHaveCount(1);
    await expect(select.locator('option', { hasText: 'Parable Party announcer' })).toHaveCount(1);
    expect(await look(page, info, 'my-voice-panel', 'my-voice')).toEqual([]);

    await select.selectOption('bf_emma');
    await expect(page.getByText('Jarvis will use Emma, British female with you')).toBeVisible();
    expect(run.sent.find((s) => s.path === '/api/users/me')?.body).toEqual({ preferred_tts_voice: 'bf_emma' });

    await panel.getByTestId('my-voice-play').click();
    await expect.poll(() => run.sent.filter((s) => s.path === '/api/tts/sample').length).toBe(1);
    expect(run.sent.find((s) => s.path === '/api/tts/sample')?.body).toEqual({ voice: 'bf_emma' });

    // Back to Jarvis's voice clears the choice rather than saving one.
    await select.selectOption('');
    await expect.poll(() => run.sent.filter((s) => s.path === '/api/users/me').length).toBe(2);
    expect(run.sent.filter((s) => s.path === '/api/users/me')[1].body).toEqual({ clear_fields: ['preferred_tts_voice'] });
    expect(run.errors).toEqual([]);
  });

  test('Admin: the speech server, and what a satellite may control when it recognises nobody', async ({ page }, info) => {
    const run = await open(page, '/admin/ops');
    const url = page.getByTestId('speech-server-url');
    await url.scrollIntoViewIfNeeded();
    await url.fill('http://192.0.2.10:8082');
    await page.getByTestId('speech-server-save').click();
    await expect.poll(() => run.sent.find((s) => s.path === '/api/admin/raven/config')?.body).toEqual({ speech_server_url: 'http://192.0.2.10:8082' });
    await url.scrollIntoViewIfNeeded();
    await settle(page);
    await page.locator('div', { has: url }).last().screenshot({ path: info.outputPath('speech-server.png') });

    await page.goto('/admin/users');
    const box = page.getByTestId('household-access');
    await expect(box).toBeVisible();
    await expect(box.getByRole('radio', { name: "Every device that isn't locked" })).toHaveAttribute('aria-checked', 'true');
    await box.getByRole('radio', { name: 'Nothing' }).click();
    await expect.poll(() => run.sent.find((s) => s.path === '/api/settings/household_device_access')?.body).toEqual({ value: 'none' });
    expect(await look(page, info, 'household-access', 'household-access')).toEqual([]);
    expect(run.errors).toEqual([]);
  });
});
