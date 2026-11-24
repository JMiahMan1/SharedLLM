import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { fakeAndroid, type FakeAndroidState } from '../fixtures/fakeAndroid';
import { quietHotReload, screenFindings, settle, shot, type Allowed } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Wander's Bluetooth features as the Android app shows them (linking a car,
 * Find family nearby, the offline family chat and the admin diagnostics), at
 * every size: the Bluetooth plugins are answered by fixtures/fakeAndroid.ts,
 * since neither the browser nor the emulator has Bluetooth. Made-up devices.
 */

const LEAFLET: Allowed[] = [{ where: /./, finding: /under 44px: (Leaflet|OpenStreetMap)\b/, why: 'map attribution' }];

const STATE: FakeAndroidState = {
  vehicle: {
    supported: true, permissionGranted: true, bluetoothOn: true, apiLevel: 28,
    linkedConnected: [{ address: '00:1A:7D:DA:71:13', vehicle: 'Family minivan with the roof box' }],
    otherConnectedCount: 1, linkedCount: 2, linksRefreshedAt: null,
  },
  devices: [
    { name: 'Hatchback Radio', address: '00:1B:DC:0F:AA:09', type: 'classic', connected: true, linkable: true,
      kind: 'classic', reason: null, linkedTo: null },
    { name: 'Wireless Earbuds With A Very Long Product Name', address: '4A:11:22:33:44:55', type: 'le', connected: false,
      linkable: false, kind: null, linkedTo: null,
      reason: "This device keeps changing its address for privacy, so the phone would not know it next time. Pair it in Android's Bluetooth settings, then link it." },
  ],
  proximity: {
    enabled: true, scanPermission: true, advertisePermission: true, bluetoothOn: true, running: true,
    advertising: 'unsupported', scanning: true, scanMode: 'low_power', driving: false, lastScanStartAt: 1,
    lastHeardAt: 2, configured: true, keyId: 'a1b2c3d4e5f6', members: 5, outbox: 0, uploadedAt: null,
    peers: [{ person: 'robin', band: 'same_room', rssi: -72, at: Date.now() }],
  },
  mesh: {
    enabled: true, connectPermission: true, room: true, server: false, syncing: null, stored: 2, unsynced: 1,
    hasMail: true, lastSentAt: Date.now(), lastReceivedAt: Date.now(), lastServerSyncAt: null,
    lastPeerSync: { at: Date.now(), peer: 'robin', sent: 1, received: 1 }, lastError: null,
  },
  messages: [
    { id: 'a'.repeat(32), t: 'text', from: 'robin', room: 'abcd1234', sent_at: 1, mine: false, synced: false, hops: 2,
      text: 'No signal up here. We are at the second lookout, all fine, back by four.' },
    { id: 'b'.repeat(32), t: 'pin', from: 'parent', room: 'abcd1234', sent_at: 2, mine: true, synced: false, hops: 0,
      lat: 39.7392, lon: -104.9903, label: 'Trailhead parking' },
  ],
};

async function open(page: Page): Promise<string[]> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await fakeAndroid(page, STATE);
  const errors: string[] = [];
  // Other native-only plugins (chat notices, media, Drop In) have no stand-in here; their
  // "not implemented on android" is the stand-in's limit, not a fault of these screens.
  page.on('pageerror', (e) => {
    if (!/plugin is not implemented on android/.test(e.message)) errors.push(e.message);
  });
  await page.goto('/wander');
  await expect(page.getByTestId('trip-card').first()).toBeVisible();
  return errors;
}

test('linking a car lists the paired devices and says why one cannot be linked', async ({ page }, info) => {
  const errors = await open(page);
  const panel = page.getByTestId('vehicle-bt-panel');
  await panel.scrollIntoViewIfNeeded();
  await panel.getByTestId('vehicle-bt-v2').getByRole('button', { name: /Link a device/ }).click();
  const list = panel.getByTestId('vehicle-bt-devices');
  await expect(list.getByRole('button', { name: /Hatchback Radio/ })).toBeEnabled();
  await expect(list.getByRole('button', { name: /Wireless Earbuds/ })).toBeDisabled();
  await expect(list).toContainText('keeps changing its address');
  await settle(page);
  expect(await screenFindings(page, panel, 'link a car')).toEqual([]);
  await shot(page, info, 'wander-android-link');

  const linked = page.waitForRequest((r) => r.url().includes('/api/geo/vehicles/v2/bluetooth') && r.method() === 'POST');
  await page.route('**/api/geo/vehicles/v2/bluetooth', (r) => r.fulfill({ json: { status: 'ok', vehicle: {} } }));
  await list.getByRole('button', { name: /Hatchback Radio/ }).click();
  expect((await linked).postDataJSON()).toEqual({ address: '00:1B:DC:0F:AA:09', kind: 'classic', name: 'Hatchback Radio' });
  expect(errors).toEqual([]);
});

test('family nearby, the offline chat and the diagnostics on a scan-only tablet', async ({ page }, info) => {
  const errors = await open(page);
  const nearby = page.getByTestId('family-nearby');
  await nearby.scrollIntoViewIfNeeded();
  await expect(page.getByTestId('family-nearby-status')).toContainText('listening only');

  const chat = page.getByTestId('offline-chat');
  await expect(chat).toContainText('No signal up here');
  await expect(chat).toContainText('Came through 1 other phone');
  await expect(chat.getByRole('link', { name: /Trailhead parking/ })).toBeVisible();
  await chat.getByLabel('Offline message', { exact: true }).fill('On our way down');
  await chat.getByRole('button', { name: 'Send offline' }).click();
  await expect(chat).toContainText('On our way down');
  const sent = await page.evaluate(() => (window as any).__nativeCalls.filter((c: any) => c.method === 'send'));
  expect(sent[0].options).toEqual({ kind: 'text', fields: { text: 'On our way down' } });

  const diag = page.getByTestId('bt-diagnostics');
  await diag.locator('summary').click();
  await expect(page.getByTestId('diag-advertiser')).toContainText('scan only');
  await expect(page.getByTestId('diag-peers')).toContainText('robin: In the same room (-72 dBm');
  await expect(page.getByTestId('diag-bt-vehicle')).toContainText('00:1A:7D:DA:71:13');
  await expect(page.getByTestId('diag-mesh-peer')).toContainText('robin');

  await settle(page);
  for (const [where, scope] of [['family nearby', nearby], ['offline chat', chat], ['diagnostics', diag]] as const) {
    expect(await screenFindings(page, scope, where, LEAFLET)).toEqual([]);
  }
  await shot(page, info, 'wander-android-nearby-chat');
  if (process.env.SNAP_DIR) {
    await chat.screenshot({ path: `${process.env.SNAP_DIR}/offline-chat-${info.project.name}.png` });
    await diag.screenshot({ path: `${process.env.SNAP_DIR}/diagnostics-${info.project.name}.png` });
  }
  expect(errors).toEqual([]);
});
