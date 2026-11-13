import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { isServerCall } from '../fixtures/serverCalls';
import { expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Room detection by Bluetooth, in every project (desktop, Pixel 7, a 360x740
 * phone, a tablet both ways round): the Map rooms walk-through on Devices, the
 * beacon and room on Devices > Phones, and the room on a Wander card.
 * Hermetic: fixtures/homeMocks.ts, plus per-test routes for the session.
 */

async function openDevices(page: Page): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/devices#devices-rooms');  // an old link: opens the Setup tab
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors };
}

function clean({ mocks, errors }: { mocks: HomeMocks; errors: string[] }) {
  expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
  expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
}

/** A recording that reports progress twice, then finishes. */
async function sessionProgress(page: Page) {
  let polls = 0;
  await page.route((url) => url.pathname === '/api/homehub/bluetooth/map/sessions/map-1', (route: Route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    polls += 1;
    const body = polls <= 2
      ? { status: 'recording', samples: 10 * polls, scanners_heard: 2, seconds_left: 60 - polls }
      : { status: 'done', samples: 58, scanners_heard: 3, seconds_left: 0 };
    return route.fulfill({ json: body });
  });
}

test.describe('Room detection', () => {
  test.describe.configure({ timeout: 90_000 });

  test('Map rooms: pick the phone, record a room, another pass, the summary, clear a room and all', async ({ page }, info) => {
    const run = await openDevices(page);
    await sessionProgress(page);
    // Room detection is on the Setup tab (the old link already opened it); the tab says so.
    await expect(page.getByTestId('devices-sections').getByRole('tab', { name: 'Setup' })).toHaveAttribute('aria-selected', 'true');
    const panel = page.getByTestId('room-map');
    await panel.scrollIntoViewIfNeeded();

    // 1. The tracker: in a browser there is no beacon of its own, so the first phone the hub follows.
    await expect(panel.getByLabel('What you will carry')).toHaveValue('sensor.robin_s_phone');
    await expect(panel.getByRole('option', { name: 'This phone' })).toHaveCount(0);

    // 2. Every hub room with its state.
    const rows = panel.getByTestId('room-row');
    await expect(rows).toHaveCount(3);
    await expect(rows.filter({ hasText: 'Living room' })).toContainText('Stale');
    await expect(rows.filter({ hasText: 'Living room' })).toContainText('90%');
    await expect(rows.filter({ hasText: 'Bedroom' })).toContainText('Not mapped');
    await expectTapTargets(panel.getByTestId('room-row').first());
    await expectNoHorizontalOverflow(page);

    // 4. The summary: overall accuracy, the rooms mixed up, why the map is stale.
    const summary = panel.getByTestId('room-map-summary');
    await expect(summary).toContainText('Overall accuracy 81%');
    await expect(summary.getByTestId('room-map-confused')).toContainText('(14 samples)');
    await expect(summary.getByTestId('room-map-confused')).toContainText('another listener');
    await expect(summary.getByTestId('room-map-stale')).toContainText('A listener was added');
    await snap(page, info, 'room-map-overview');

    // 3. Record the bedroom: instructions, Start, the countdown with live counts, Done.
    await panel.getByRole('button', { name: 'Map Bedroom' }).click();
    await expect(panel.getByText(/Walk slowly around the room, including corners, holding the phone as you normally carry it/))
      .toBeVisible();
    await panel.getByTestId('room-map-start').click();
    await expect.poll(() => run.mocks.posts.find((p) => p.path === '/api/homehub/bluetooth/map/sessions')?.body)
      .toEqual({ area_id: 'bedroom', tracker: 'sensor.robin_s_phone', duration_s: 60 });
    await expect(panel.getByTestId('room-map-countdown')).toBeVisible();
    await expect(panel.getByTestId('room-map-samples')).toHaveText(/10|20/);
    await expect(panel.getByTestId('room-map-scanners')).toHaveText('2');
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'room-map-recording');
    const done = panel.getByTestId('room-map-done');
    await expect(done).toContainText('58 samples from 3 listeners');
    await expect(done).toContainText('More passes give better accuracy');
    await snap(page, info, 'room-map-done');

    // Another pass starts from the instructions again; back to the rooms.
    await done.getByRole('button', { name: 'Record another pass' }).click();
    await expect(panel.getByTestId('room-map-start')).toBeVisible();
    await panel.getByRole('button', { name: 'Rooms' }).click();

    // 6. Clear one room, then all, each after asking.
    await panel.getByRole('button', { name: 'Clear Living room' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Clear', exact: true }).click();
    await expect.poll(() => run.mocks.posts.some((p) => p.path === '/api/homehub/bluetooth/map/living')).toBe(true);
    await panel.getByRole('button', { name: 'Clear all' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Clear the whole room map?');
    await dialog.getByRole('button', { name: 'Clear all' }).click();
    await expect.poll(() => run.mocks.posts.some((p) => p.path === '/api/homehub/bluetooth/map')).toBe(true);
    clean(run);
  });

  test('a tracker nobody hears is said plainly', async ({ page }, info) => {
    const run = await openDevices(page);
    await page.route((url) => url.pathname === '/api/homehub/bluetooth/map/sessions', (route) =>
      route.fulfill({ status: 409, json: { detail: 'sensor.robin_s_phone has not been heard by any listener in the last 30 s' } }));
    const panel = page.getByTestId('room-map');
    await panel.scrollIntoViewIfNeeded();
    await panel.getByRole('button', { name: 'Map Bedroom' }).click();
    await panel.getByTestId('room-map-start').click();
    const error = panel.getByTestId('room-map-error');
    await expect(error).toContainText("That tracker isn't being heard");
    await expect(error).toContainText('has not been heard by any listener');
    await expect(panel.getByTestId('room-map-start')).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'room-map-not-heard');
    clean(run);
  });

  test("Devices > Phones shows the phone's beacon and its room", async ({ page }) => {
    const run = await openDevices(page);
    const phone = page.getByTestId('phones').getByTestId('phone').first();
    await phone.scrollIntoViewIfNeeded();
    await expect(phone.getByTestId('phone-beacon')).toHaveText(/Room beacon: on/);
    await expect(phone.getByTestId('phone-room-sensor')).toContainText('Room: Bedroom');
    await expectNoHorizontalOverflow(page);
    clean(run);
  });

  test("a family card shows the room the hub places a person in, never a stale one as current", async ({ page }) => {
    await mockHome(page, 'admin');
    // Wander's own calls: a person with a room, one with a stale room; anything else empty.
    await page.route(isServerCall, (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p === '/api/geo/people') {
        return route.fulfill({ json: { type: 'FeatureCollection', features: [
          { type: 'Feature', geometry: null, properties: { entity_id: 'person.alex', friendly_name: 'Alex', state: 'home',
            last_updated: new Date().toISOString(), room: 'Den', room_area_id: 'den', room_last_seen: new Date().toISOString(), room_stale: false } },
          { type: 'Feature', geometry: null, properties: { entity_id: 'person.sam', friendly_name: 'Sam', state: 'home',
            last_updated: new Date().toISOString(), room: 'Loft', room_area_id: 'loft',
            room_last_seen: new Date(Date.now() - 45 * 60_000).toISOString(), room_stale: true } },
        ] } });
      }
      if (p.startsWith('/api/users/') || p === '/api/telemetry/notifications' || p === '/api/info' || p === '/api/health') return route.fallback();
      return route.fulfill({ json: {} });
    });
    await page.goto('/wander');
    const rooms = page.getByTestId('member-room');
    await expect(rooms.filter({ hasText: 'Den' }).first()).toHaveText('Den');
    await expect(rooms.filter({ hasText: 'Loft' }).first()).toHaveText('Last seen in Loft, 45 min ago');
    await expectNoHorizontalOverflow(page);
  });
});
