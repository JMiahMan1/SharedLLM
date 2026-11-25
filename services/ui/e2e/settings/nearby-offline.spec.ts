import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { fakeAndroid, mockNearbySettings } from '../fixtures/fakeAndroid';
import { quietHotReload, screenFindings, settle, shot, type Allowed } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Settings > Nearby & offline on every form factor, in the Android app (the
 * Bluetooth plugins answered by fixtures/fakeAndroid.ts): a grown member's
 * switches, a child's read-only ones with the parent link, and a parent
 * setting a child's from Guardian. Thumb-sized controls, nothing cut off or
 * past the edge. Made-up people.
 */

const LEAFLET: Allowed[] = [{ where: /./, finding: /under 44px: (Leaflet|OpenStreetMap)\b/, why: 'map attribution' }];

type Call = { plugin: string; method: string; options: Record<string, unknown> };

async function open(page: Page, role: 'admin' | 'member', path: string): Promise<{ home: HomeMocks; errors: string[] }> {
  await quietHotReload(page);
  const home = await mockHome(page, role);
  await mockHealth(page);
  await mockBible(page);
  if (path.startsWith('/settings')) await mockSettingsPages(page);
  else await mockFamilyPages(page);
  await fakeAndroid(page, { nearby: role === 'member' ? { parentLink: true } : {} });
  await mockNearbySettings(page, role === 'member' ? { nearby: true } : {}, { minor: role === 'member' });
  const errors: string[] = [];
  // Other native-only plugins have no stand-in here; "not implemented on android" is the stand-in's limit.
  page.on('pageerror', (e) => {
    if (!/plugin is not implemented on android/.test(e.message)) errors.push(e.message);
  });
  await page.goto(path);
  return { home, errors };
}

const calls = (page: Page) =>
  page.evaluate(() => ((window as Window & { __nativeCalls?: Call[] }).__nativeCalls ?? []).filter((c) => c.plugin === 'JarvisNearby'));

test('a grown member switches a part on: saved, applied on the phone, asked for then', async ({ page }, info) => {
  const { errors } = await open(page, 'admin', '/settings#nearby-offline');
  const panel = page.getByTestId('nearby-offline-panel');
  await panel.scrollIntoViewIfNeeded();
  for (const name of ['Find family nearby', 'Offline family chat', 'Offline mode', 'Car detection']) {
    await expect(panel.getByRole('switch', { name })).toBeEnabled();
  }
  await expect(panel.getByRole('switch', { name: 'Car detection' })).toHaveAttribute('aria-checked', 'true');
  await expect(panel).not.toContainText('Jarvis Android app');

  const saved = page.waitForRequest((r) => r.url().endsWith('/api/users/me/nearby-settings') && r.method() === 'PUT');
  await panel.getByRole('switch', { name: 'Offline mode' }).click();
  expect((await saved).postDataJSON()).toEqual({ settings: { offline: true } });
  await expect(panel.getByRole('switch', { name: 'Offline mode' })).toHaveAttribute('aria-checked', 'true');
  await expect.poll(async () => (await calls(page)).filter((c) => c.method === 'apply').at(-1)?.options)
    .toEqual({ nearby: false, chat: false, offline: true, car: true, ask: true });
  // Location sharing is off here: the engine runs on its own, with one quiet notification.
  await expect(page.getByTestId('nearby-device-line')).toContainText('location sharing is off');
  await expect(page.getByTestId('nearby-offline-mode')).toBeVisible();

  await settle(page);
  expect(await screenFindings(page, panel, 'nearby & offline', LEAFLET)).toEqual([]);
  await shot(page, info, 'settings-nearby-offline');
  expect(errors).toEqual([]);
});

test("a child sees the switches read-only, with the parent link always on", async ({ page }, info) => {
  const { errors } = await open(page, 'member', '/settings#nearby-offline');
  const panel = page.getByTestId('nearby-offline-panel');
  await panel.scrollIntoViewIfNeeded();
  await expect(page.getByTestId('nearby-managed')).toContainText('A parent manages these');
  for (const name of ['Find family nearby', 'Offline family chat', 'Offline mode', 'Car detection']) {
    await expect(panel.getByRole('switch', { name })).toBeDisabled();
  }
  await expect(page.getByTestId('nearby-parent-link')).toContainText('lock, unlock or give more time');
  await settle(page);
  expect(await screenFindings(page, panel, 'nearby & offline (child)', LEAFLET)).toEqual([]);
  await shot(page, info, 'settings-nearby-offline-child');
  expect(errors).toEqual([]);
});

test("a parent sets a child's parts in Guardian", async ({ page }, info) => {
  const { errors } = await open(page, 'admin', '/guardian?area=safety&member=jordan');
  const card = page.getByTestId('guardian-nearby');
  await card.scrollIntoViewIfNeeded();
  const saved = page.waitForRequest((r) => r.url().endsWith('/api/users/jordan/nearby-settings') && r.method() === 'PUT');
  await card.getByRole('switch', { name: 'Find family nearby' }).click();
  expect((await saved).postDataJSON()).toEqual({ settings: { nearby: true } });
  await expect(card.getByRole('switch', { name: 'Find family nearby' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('guardian-nearby-parent-link')).toContainText('Always on');
  // This parent's own offline mode is off: Guardian points to Settings for the offline controls.
  await expect(page.getByTestId('guardian-offline-off')).toContainText('Settings, Nearby & offline');
  await settle(page);
  expect(await screenFindings(page, card, 'guardian nearby card', LEAFLET)).toEqual([]);
  expect(await screenFindings(page, page.getByTestId('guardian-offline-controls'), 'guardian offline controls', LEAFLET)).toEqual([]);
  await shot(page, info, 'guardian-nearby');
  expect(errors).toEqual([]);
});
