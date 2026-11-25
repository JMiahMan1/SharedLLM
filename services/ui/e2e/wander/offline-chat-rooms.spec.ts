import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages } from '../fixtures/familyPagesMocks';
import { fakeAndroid, mockNearbySettings } from '../fixtures/fakeAndroid';
import { quietHotReload, screenFindings, settle, shot, type Allowed } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * The offline chat in every chat room, as the Android app shows it with no
 * internet, at every size: the inbox lists the rooms this phone holds a key
 * for, a one-to-one room and a group room each take a message with their own
 * composer, and each room shows only its own offline messages with where
 * they are. No room is set to open on launch. Bluetooth: fixtures/fakeAndroid.ts.
 * Made-up family.
 */

const LEAFLET: Allowed[] = [{ where: /./, finding: /under 44px: (Leaflet|OpenStreetMap)\b/, why: 'map attribution' }];

type Call = { plugin: string; method: string; options: Record<string, unknown> };

async function open(page: Page): Promise<string[]> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await fakeAndroid(page, {
    nearby: { nearby: true, chat: true, offline: true, car: true, host: 'own' },
    mesh: { enabled: true, connectPermission: true, rooms: 2, server: true, syncing: null, stored: 1, unsynced: 0, hasMail: true,
      lastSentAt: null, lastReceivedAt: Date.now(), lastServerSyncAt: null, lastError: null },
    meshRooms: [
      { room: 'room-jordan', name: 'Jordan', type: 1 },
      { room: 'room-trip', name: 'Camping trip planning', type: 2 },
    ],
    messages: [
      { id: 'a'.repeat(32), t: 'text', from: 'robin', room: 'room-trip', sent_at: 1, mine: false, synced: false, hops: 2,
        text: 'No signal up here. We are at the second lookout, all fine, back by four.' },
    ],
    offline: { peerNearby: true, cache: [], requests: [], commands: [], diag: { online: false, peerNearby: true } },
  });
  await mockNearbySettings(page, { nearby: true, chat: true, offline: true });
  // No internet: the health check and the chat server get no answer at all.
  await page.route('**/health/ready', (r) => r.abort('internetdisconnected'));
  await page.route('**/api/communication/talk/**', (r) => r.abort('internetdisconnected'));
  const errors: string[] = [];
  page.on('pageerror', (e) => {
    if (!/plugin is not implemented on android/.test(e.message)) errors.push(e.message);
  });
  await page.goto('/family?tab=chat');
  return errors;
}

async function sends(page: Page): Promise<Call[]> {
  return page.evaluate(() =>
    ((window as Window & { __nativeCalls?: Call[] }).__nativeCalls ?? []).filter((c) => c.plugin === 'JarvisFamilyMesh' && c.method === 'send'));
}

async function openRoom(page: Page, name: RegExp) {
  const list = page.getByRole('list', { name: 'Conversations' });
  if (!(await list.isVisible())) await page.getByRole('button', { name: 'Back to chats' }).click();
  await list.getByRole('button', { name }).first().click();
}

test.setTimeout(150_000);

test('offline: a one-to-one room and a group room each send into their own room', async ({ page }, info) => {
  const errors = await open(page);
  const notice = page.getByTestId('chat-offline');
  await expect(notice).toContainText('No internet');
  await expect(notice).not.toContainText(/launch|default/i);

  // The one-to-one room.
  await openRoom(page, /Jordan/);
  const thread = page.getByTestId('chat-thread');
  const composer = thread.getByTestId('offline-room-composer');
  await expect(composer).toBeVisible();
  // The group's message is not shown in someone else's room.
  await expect(thread).not.toContainText('second lookout');
  await composer.getByLabel('Offline message', { exact: true }).fill('Meet at the car at five');
  await composer.getByRole('button', { name: 'Send offline' }).click();
  const mine = thread.getByTestId('offline-room-messages');
  await expect(mine).toContainText('Meet at the car at five');
  await expect(mine).toContainText('Waiting for the internet');
  await settle(page);
  expect(await screenFindings(page, composer, 'offline composer', LEAFLET)).toEqual([]);
  expect(await screenFindings(page, mine, 'offline one-to-one messages', LEAFLET)).toEqual([]);
  await shot(page, info, 'chat-offline-one-to-one');

  // The group room.
  await openRoom(page, /Camping trip planning/);
  const group = page.getByTestId('chat-thread').getByTestId('offline-room-messages');
  await expect(group).toContainText('second lookout');
  await expect(group).toContainText('Came through 1 other phone');
  await expect(group).not.toContainText('Meet at the car');
  const groupComposer = page.getByTestId('offline-room-composer');
  await groupComposer.getByLabel('Offline message', { exact: true }).fill('Coming up the trail now');
  await groupComposer.getByRole('button', { name: 'Send offline' }).click();
  await expect(group).toContainText('Coming up the trail now');
  await settle(page);
  expect(await screenFindings(page, group, 'offline group messages', LEAFLET)).toEqual([]);
  expect(await screenFindings(page, groupComposer, 'offline group composer', LEAFLET)).toEqual([]);
  await shot(page, info, 'chat-offline-group');

  const sent = await sends(page);
  expect(sent.map((c) => c.options)).toEqual([
    { room: 'room-jordan', kind: 'text', fields: { text: 'Meet at the car at five' } },
    { room: 'room-trip', kind: 'text', fields: { text: 'Coming up the trail now' } },
  ]);
  expect(errors).toEqual([]);
});
