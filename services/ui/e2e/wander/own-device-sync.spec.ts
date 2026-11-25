import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockSettingsPages } from '../fixtures/settingsPagesMocks';
import { fakeAndroid, mockNearbySettings, type FakeAndroidState } from '../fixtures/fakeAndroid';
import { quietHotReload, screenFindings, settle, shot, type Allowed } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * My devices, as the Android app shows it at every size: the switch in
 * Settings > Nearby & offline, and Admin > Users & Devices > Bluetooth
 * diagnostics listing each of the person's other devices with when it last
 * synced and, per kind of data, "In sync" or "Updated from <device> at <time>".
 * The Bluetooth plugins are answered by fixtures/fakeAndroid.ts. Made-up devices.
 */

const LEAFLET: Allowed[] = [{ where: /./, finding: /under 44px: (Leaflet|OpenStreetMap)\b/, why: 'map attribution' }];

const AT = new Date(2026, 10, 25, 15, 42).getTime();

const STATE: FakeAndroidState = {
  vehicle: {
    supported: true, permissionGranted: true, bluetoothOn: true, apiLevel: 37,
    linkedConnected: [], otherConnectedCount: 0, linkedCount: 0, linksRefreshedAt: null,
  },
  nearby: { mine: true, host: 'own' },
  own: {
    diag: {
      enabled: true, turnedOn: true, deviceId: 'phone-0001', name: 'Test Phone Seventeen', ownItems: 6,
      peers: [
        {
          device: 'tablet-0002', name: 'Test Tablet With A Rather Long Model Name', last_sync_at: AT, first_seen_at: AT - 86_400_000,
          types: {
            device: { status: 'updated', from: 'Test Tablet With A Rather Long Model Name', at: AT },
            steps: { status: 'updated', from: 'Test Tablet With A Rather Long Model Name', at: AT },
            watch: { status: 'in_sync', at: AT },
            rooms: { status: 'in_sync', at: AT },
            family: { status: 'updated', from: 'Test Tablet With A Rather Long Model Name', at: AT },
            chat: { status: 'in_sync', at: AT },
            requests: { status: 'in_sync', at: AT },
            guardian: { status: 'in_sync', at: AT },
          },
        },
      ],
    },
    state: {
      enabled: true, deviceId: 'phone-0001', devices: [],
      steps: { day: '2026-11-25', total: 7420, devices: [
        { device: 'phone-0001', name: 'Test Phone Seventeen', me: true, steps: 5120, at: AT },
        { device: 'tablet-0002', name: 'Test Tablet With A Rather Long Model Name', me: false, steps: 1300, at: AT },
        { device: 'watch', name: 'Jarvis Watch', me: false, steps: 7420, at: AT },
      ] },
      latestFix: { lat: 39.7, lon: -105, at: AT, device: 'tablet-0002', name: 'Test Tablet With A Rather Long Model Name' },
    },
  },
};

async function open(page: Page, path: string): Promise<string[]> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  if (path.startsWith('/settings')) await mockSettingsPages(page);
  await fakeAndroid(page, STATE);
  await mockNearbySettings(page, { mine: true });
  // The admin check (PIN or fingerprint) passed a moment ago in this app session.
  await page.addInitScript(() => sessionStorage.setItem('jarvis_admin_elevated_until', String(Date.now() + 600_000)));
  const errors: string[] = [];
  page.on('pageerror', (e) => {
    if (!/plugin is not implemented on android/.test(e.message)) errors.push(e.message);
  });
  await page.goto(path);
  return errors;
}

test('diagnostics list each of my other devices: last sync, and per kind in sync or where it came from', async ({ page }, info) => {
  const errors = await open(page, '/admin/users');
  const diag = page.getByTestId('bt-diagnostics');
  await diag.scrollIntoViewIfNeeded();
  await diag.locator('summary').click();
  const own = diag.getByTestId('diag-own');
  await expect(own).toBeVisible();
  await expect(own.getByTestId('diag-own-on')).toContainText('Yes');
  await expect(own.getByTestId('diag-own-steps')).toContainText('7,420');
  await expect(own.getByTestId('diag-own-fix')).toContainText('From Test Tablet');
  const peer = own.getByTestId('diag-own-peer');
  await expect(peer).toHaveCount(1);
  await expect(peer).toContainText('Test Tablet With A Rather Long Model Name');
  await expect(peer.getByTestId('diag-own-peer-synced')).toContainText('Nov 25');
  await expect(peer.getByTestId('diag-own-type-steps')).toContainText('Updated from Test Tablet With A Rather Long Model Name at Nov 25');
  await expect(peer.getByTestId('diag-own-type-watch')).toContainText('In sync');
  await expect(peer.getByTestId('diag-own-type-guardian')).toContainText('In sync');

  await own.scrollIntoViewIfNeeded();
  await settle(page);
  expect(await screenFindings(page, own, 'my devices diagnostics', LEAFLET)).toEqual([]);
  await shot(page, info, 'admin-bt-diagnostics-my-devices');
  expect(errors).toEqual([]);
});

test('Settings has the My devices switch, and turning it on applies it on the phone', async ({ page }, info) => {
  const errors = await open(page, '/settings#nearby-offline');
  const panel = page.getByTestId('nearby-offline-panel');
  await panel.scrollIntoViewIfNeeded();
  const sw = panel.getByRole('switch', { name: 'My devices' });
  await expect(sw).toHaveAttribute('aria-checked', 'true');
  const saved = page.waitForRequest((r) => r.url().endsWith('/api/users/me/nearby-settings') && r.method() === 'PUT');
  await sw.click();
  expect((await saved).postDataJSON()).toEqual({ settings: { mine: false } });
  await expect(sw).toHaveAttribute('aria-checked', 'false');
  await settle(page);
  expect(await screenFindings(page, panel, 'nearby & offline with my devices', LEAFLET)).toEqual([]);
  await shot(page, info, 'settings-my-devices');
  expect(errors).toEqual([]);
});
