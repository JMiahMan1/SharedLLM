import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { fakeAndroid, mockNearbySettings, type FakeAndroidState } from '../fixtures/fakeAndroid';
import { quietHotReload, screenFindings, settle, shot, type Allowed } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The Bluetooth features as the Android app shows them, at every size:
 * linking a car (Wander), and the offline chat in each chat room (Chat,
 * switched on in Settings > Nearby & offline). The Bluetooth plugins are answered by
 * fixtures/fakeAndroid.ts, since neither the browser nor the emulator has
 * Bluetooth. Made-up devices. The admin diagnostics moved to Admin > Users &
 * Devices (PIN-gated in the app; covered by BluetoothDiagnostics.test.tsx).
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
  nearby: { nearby: true, chat: true, offline: false, car: true, host: 'location' },
  mesh: {
    enabled: true, connectPermission: true, rooms: 2, server: false, syncing: null, stored: 2, unsynced: 1,
    hasMail: true, lastSentAt: Date.now(), lastReceivedAt: Date.now(), lastServerSyncAt: null,
    lastPeerSync: { at: Date.now(), peer: 'robin', sent: 1, received: 1 }, lastError: null,
  },
  meshRooms: [{ room: 'room-family', name: 'The Family Group', type: 2 }, { room: 'room-jordan', name: 'Jordan', type: 1 }],
  messages: [
    { id: 'a'.repeat(32), t: 'text', from: 'robin', room: 'room-family', sent_at: 1, mine: false, synced: false, hops: 2,
      text: 'No signal up here. We are at the second lookout, all fine, back by four.' },
    { id: 'b'.repeat(32), t: 'pin', from: 'parent', room: 'room-family', sent_at: 2, mine: true, synced: false, hops: 0,
      lat: 39.7392, lon: -104.9903, label: 'Trailhead parking' },
  ],
};

async function open(page: Page, path = '/wander'): Promise<string[]> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await fakeAndroid(page, STATE);
  await mockNearbySettings(page, { nearby: true, chat: true });
  const errors: string[] = [];
  // Other native-only plugins (chat notices, media, Drop In) have no stand-in here; their
  // "not implemented on android" is the stand-in's limit, not a fault of these screens.
  page.on('pageerror', (e) => {
    if (!/plugin is not implemented on android/.test(e.message)) errors.push(e.message);
  });
  await page.goto(path);
  if (path === '/wander') await expect(page.getByTestId('trip-card').first()).toBeVisible();
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

test('Wander keeps only the car link and points to Nearby & offline in Settings', async ({ page }) => {
  const errors = await open(page);
  await expect(page.getByTestId('vehicle-bt-panel')).toBeVisible();
  await expect(page.getByTestId('family-nearby')).toHaveCount(0);
  await expect(page.getByTestId('offline-chat')).toHaveCount(0);
  await expect(page.getByTestId('offline-mode')).toHaveCount(0);
  await expect(page.getByTestId('bt-diagnostics')).toHaveCount(0);
  await expect(page.getByTestId('wander-nearby-link')).toHaveAttribute('href', '/settings#nearby-offline');
  expect(errors).toEqual([]);
});

test('online, a room shows its own offline messages not yet posted, and the usual composer', async ({ page }, info) => {
  const errors = await open(page, '/family?tab=chat');
  // No offline notice and no offline composer while the server answers.
  await expect(page.getByTestId('chat-offline')).toHaveCount(0);
  const list = page.getByRole('list', { name: 'Conversations' });
  await list.getByRole('button', { name: /The Family Group/ }).first().click();
  const thread = page.getByTestId('chat-thread');
  const offline = thread.getByTestId('offline-room-messages');
  await expect(offline).toContainText('No signal up here');
  await expect(offline).toContainText('Came through 1 other phone');
  await expect(offline.getByRole('link', { name: /Trailhead parking/ })).toBeVisible();
  await expect(thread.getByTestId('offline-room-composer')).toHaveCount(0);
  // The phone is asked for the rooms' keys again while online.
  type Call = { plugin: string; method: string };
  await expect.poll(async () => page.evaluate(() =>
    ((window as Window & { __nativeCalls?: Call[] }).__nativeCalls ?? []).some((c) => c.method === 'refreshRooms'))).toBe(true);

  await settle(page);
  expect(await screenFindings(page, offline, 'offline messages in a room', LEAFLET)).toEqual([]);
  await shot(page, info, 'chat-android-offline-messages-online');
  expect(errors).toEqual([]);
});
