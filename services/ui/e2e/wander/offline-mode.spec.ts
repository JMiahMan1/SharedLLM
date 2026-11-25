import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { fakeAndroid } from '../fixtures/fakeAndroid';
import { quietHotReload, screenFindings, settle, shot, type Allowed } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * Offline mode as the Android app shows it when the server cannot be reached,
 * at every size: the banner, Jarvis questions answered from saved information
 * or queued, shopping edits shown until confirmed, and a parent locking a child
 * nearby with delivery status. Bluetooth plugins: fixtures/fakeAndroid.ts.
 * Made-up family and data.
 */

const LEAFLET: Allowed[] = [{ where: /./, finding: /under 44px: (Leaflet|OpenStreetMap)\b/, why: 'map attribution' }];
const AT = new Date();
AT.setHours(15, 41, 0, 0);

async function open(page: Page): Promise<string[]> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await fakeAndroid(page, {
    vehicle: { supported: true, permissionGranted: true, bluetoothOn: true, apiLevel: 37, linkedConnected: [], otherConnectedCount: 0,
      linkedCount: 0, linksRefreshedAt: null },
    proximity: { enabled: true, scanPermission: true, advertisePermission: true, bluetoothOn: true, running: true, advertising: 'on',
      scanning: true, scanMode: 'low_power', driving: false, lastScanStartAt: 1, lastHeardAt: 2, configured: true, keyId: 'k',
      members: 4, outbox: 0, uploadedAt: null, peers: [] },
    mesh: { enabled: true, connectPermission: true, room: true, server: true, syncing: null, stored: 0, unsynced: 0, hasMail: false,
      lastSentAt: null, lastReceivedAt: null, lastServerSyncAt: null, lastError: null },
    messages: [],
    offline: {
      peerNearby: true,
      cache: [
        { key: 'locations', version: AT.getTime(), data: { locations: [{ username: 'robin', display_name: 'Robin', zone: 'Riverside Elementary School' }] } },
        { key: 'shopping', version: AT.getTime(), data: { text: '- [ ] Milk\n- [x] Eggs\n- [ ] A very long shopping item name that should wrap on a small phone screen' } },
      ],
      requests: [],
      commands: [],
      diag: { online: false, peerNearby: true, queued: 0, carrying: 1, cacheItems: 2, commandsWaiting: 0, snapshotAt: AT.getTime(),
        lastRelayAt: null, parentKeys: 0, keysVersion: '', protectedPhone: false },
    },
  });
  // The server cannot be reached: the health check gets no answer at all.
  await page.route('**/health/ready', (r) => r.abort('internetdisconnected'));
  const errors: string[] = [];
  page.on('pageerror', (e) => {
    if (!/plugin is not implemented on android/.test(e.message)) errors.push(e.message);
  });
  await page.goto('/wander');
  await expect(page.getByTestId('trip-card').first()).toBeVisible();
  return errors;
}

test.setTimeout(150_000);

test('offline: the banner, answers from saved information, queued questions and shopping edits', async ({ page }, info) => {
  const errors = await open(page);
  // The app's own health check decides (its retries take a while before it gives up).
  await expect(page.getByTestId('offline-mode-banner')).toHaveText(/Offline, using nearby family phones/, { timeout: 90_000 });
  const panel = page.getByTestId('offline-mode');
  await panel.scrollIntoViewIfNeeded();
  await expect(page.getByTestId('offline-mode-status')).toHaveText('Offline, using nearby family phones.');

  await panel.getByLabel('Question or command for Jarvis').fill('Where is Robin?');
  await panel.getByRole('button', { name: 'Ask' }).click();
  await expect(page.getByTestId('offline-local-answer')).toContainText('Robin was at Riverside Elementary School (as of 3:41 PM');

  await panel.getByLabel('Question or command for Jarvis').fill('Turn off the porch light');
  await panel.getByRole('button', { name: 'Ask' }).click();
  await expect(page.getByTestId('offline-requests')).toContainText('Waiting for a family phone that is online');

  await panel.getByLabel('Add to the shopping list').fill('Bread');
  await panel.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByTestId('offline-shopping').getByRole('button', { name: /Bread/ })).toContainText('Not confirmed yet');

  await settle(page);
  expect(await screenFindings(page, panel, 'offline mode', LEAFLET)).toEqual([]);
  await shot(page, info, 'wander-offline-mode');
  expect(errors).toEqual([]);
});

test('offline: a parent locks a child nearby and sees it has not reached the phone yet', async ({ page }, info) => {
  const errors = await open(page);
  const parent = page.getByTestId('offline-parent');
  await parent.scrollIntoViewIfNeeded();
  const kid = page.getByTestId('offline-child-jordan');
  await kid.getByRole('button', { name: /Lock/ }).click();
  await expect(kid.getByTestId('offline-delivery')).toContainText('Not reached yet', { timeout: 20_000 });
  await settle(page);
  expect(await screenFindings(page, parent, 'offline parent controls', LEAFLET)).toEqual([]);
  await shot(page, info, 'wander-offline-parent');
  expect(errors).toEqual([]);
});
