import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { controlsOffScreen, expectFits, expectNoHorizontalOverflow, expectTapTargets } from '../fixtures/layoutChecks';

/**
 * Video calls on every form factor: the Calls tab and a chat offer a video call
 * beside the audio one, an incoming video call offers Decline, Audio only and
 * Answer, and the Drop In settings carry a separate "camera on for a video Drop
 * In" switch per device. Nothing may sit off the screen. Made-up family.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const CONVERSATIONS = [
  { token: 'room-family', display_name: 'The Family Group With A Long Name For Small Phones', type: 2, last_activity: 3 },
  { token: 'room-robin', display_name: 'Robin', name: 'robin', type: 1, last_activity: 2 },
];

interface Mocks { puts: { url: string; body: unknown }[]; rings: unknown[] }

async function setUp(page: Page, { ringing = false } = {}): Promise<Mocks> {
  await mockHome(page, 'admin');
  const mocks: Mocks = { puts: [], rings: [] };
  const json = (route: Route, body: unknown) => route.fulfill({ json: body });
  await page.route('**/api/communication/talk/conversations', (r) => json(r, { status: 'SUCCESS', detail: { conversations: CONVERSATIONS } }));
  await page.route('**/api/communication/talk/conversations/*/messages**', (r) => json(r, { status: 'SUCCESS', detail: { messages: [] } }));
  await page.route('**/api/communication/calls**', (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'PUT') {
      mocks.puts.push({ url: url.pathname, body: route.request().postDataJSON() });
      return json(route, { ok: true });
    }
    if (url.pathname.endsWith('/ring')) { mocks.rings.push(route.request().postDataJSON()); return json(route, { ringing: ['robin'] }); }
    if (url.pathname === '/api/communication/calls/drop-in') {
      return json(route, {
        me: 'parent',
        people: [{ username: 'parent', name: 'Parent', minor: false }, { username: 'robin', name: 'Robin', minor: true }],
        settings: [
          { username: 'parent', name: 'Parent', minor: false, allow_from: ['robin'], configured: true, can_edit: true, devices: [
            { device_key: 'parent-phone', label: 'Parent’s phone with a long device name', kind: 'phone', drop_in: true, drop_in_video: false }] },
          { username: 'robin', name: 'Robin', minor: true, allow_from: ['parent'], configured: true, can_edit: true, devices: [
            { device_key: 'robin-tablet', label: 'Robin’s tablet', kind: 'tablet', drop_in: true, drop_in_video: false }] },
        ],
      });
    }
    if (url.pathname === '/api/communication/calls') {
      return json(route, { calls: ringing ? [{ token: 'room-robin', name: 'Robin', started_at: 1, ringing: true, caller: 'Robin',
        drop_in: false, video: true, participants: [{ id: 'r1', username: 'robin', name: 'Robin', muted: false, camera: true }] }] : [],
      device_drop_in: true, device_drop_in_video: false });
    }
    return route.fallback();
  });
  return mocks;
}

async function fitsEverywhere(page: Page, label: string) {
  expect(await controlsOffScreen(page), label).toEqual([]);
  await expectNoHorizontalOverflow(page);
}

test.describe('video calls', () => {
  test.describe.configure({ timeout: 90_000 });

  test('the Calls tab offers a video call beside each audio call', async ({ page }) => {
    await setUp(page);
    await page.goto('/family?tab=calls');
    const row = page.getByTestId('family-call-room-family');
    await expect(row).toBeVisible();
    const video = page.getByTestId('family-video-call-room-family');
    await expect(video).toBeInViewport({ ratio: 1 });
    await expect(page.getByTestId('family-video-call-room-robin')).toBeInViewport({ ratio: 1 });
    await expectTapTargets(row);
    await fitsEverywhere(page, 'the Calls tab');
  });

  test('a chat offers a call and a video call in its header', async ({ page }) => {
    await setUp(page);
    await page.goto('/family?chat=room-family');
    await expect(page.getByTestId('chat-video-call')).toBeInViewport({ ratio: 1 });
    await expect(page.getByTestId('chat-call')).toBeInViewport({ ratio: 1 });
    await expectTapTargets(page.getByTestId('chat-video-call'));
    await fitsEverywhere(page, 'the chat');
  });

  test('an incoming video call says so and offers Decline, Audio only and Answer', async ({ page }) => {
    await setUp(page, { ringing: true });
    await page.goto('/family?tab=calls');
    const incoming = page.getByTestId('incoming-call');
    await expect(incoming).toBeVisible();
    await expect(incoming).toContainText('Family video call');
    await expect(incoming).toContainText('is video calling you');
    for (const name of ['Decline', 'Answer with sound only', 'Answer with video']) {
      await expect(incoming.getByRole('button', { name })).toBeInViewport({ ratio: 1 });
    }
    await expectTapTargets(incoming);
    await expectFits(page, incoming);
    await fitsEverywhere(page, 'the incoming call');
  });

  test('Drop In settings carry a separate camera switch per device', async ({ page }) => {
    const mocks = await setUp(page);
    await page.goto('/settings#family-drop-in');
    const card = page.getByTestId('family-drop-in');
    await card.scrollIntoViewIfNeeded();
    const robinVideo = card.getByTestId('drop-in-video-robin-tablet');
    await expect(robinVideo).toBeVisible();
    await expect(card.getByTestId('drop-in-device-row-robin-tablet')).toContainText('Camera on for a video Drop In');
    await expectTapTargets(card.getByTestId('drop-in-device-row-robin-tablet'));
    await robinVideo.click();
    await expect.poll(() => mocks.puts.map((p) => p.body)).toContainEqual({ drop_in_video: true });
    expect(mocks.puts.find((p) => JSON.stringify(p.body).includes('drop_in_video'))!.url)
      .toBe('/api/communication/calls/drop-in/robin/devices/robin-tablet');
    await fitsEverywhere(page, 'the settings page');
  });
});
