import { test, expect as baseExpect, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, CONVERSATIONS } from '../fixtures/familyPagesMocks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';
import { expectFits, expectNoHorizontalOverflow, expectTapTargets } from '../fixtures/layoutChecks';
import { deriveKey, makeVerifier, randomBytes, toBase64, PBKDF2_ITERATIONS } from '../../src/lib/privateChatCrypto';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * Calls in the parents' private chat on every form factor. A locked chat never connects a call: it asks to
 * unlock first, with a way to the chat. An unlocked one checks the other device with the chat's key, and a
 * peer that never proves itself (a stand-in gateway that relays nothing back) gets the call ended as "could
 * not be verified". Hermetic: the gateway and its signalling socket are mocked; the microphone is a tone.
 */

const PASSWORD = 'a long shared sentence';
const TOKEN = 'roomprivate1';
const SEC = Math.floor(Date.now() / 1000);

interface Signals { sockets: number; sent: Array<Record<string, unknown>> }

async function setUp(page: Page): Promise<Signals> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(PASSWORD, kdf);
  const chat = { token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin',
    created_at: SEC - 86400, version: 1, kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key) };
  await page.route(/\/api\/(private-chat\/|communication\/talk\/(conversations|messages)(\?|$))/, async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    if (p === '/api/private-chat/status') {
      return route.fulfill({ json: { eligible: true, reason: null, chats: [chat], min_iterations: PBKDF2_ITERATIONS,
        partners: [{ username: 'robin', display_name: 'Robin' }] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}`) return route.fulfill({ json: chat });
    if (p.startsWith(`/api/private-chat/chats/${TOKEN}/gallery`)) return route.fulfill({ json: { entries: [] } });
    if (p.startsWith(`/api/private-chat/chats/${TOKEN}/marks`)) return route.fulfill({ json: { marks: [] } });
    if (p === '/api/communication/talk/conversations') {
      const own = { token: TOKEN, display_name: 'Private chat', type: 2, private: true, last_message: 'Encrypted message',
        last_activity: SEC - 30, unread_messages: 0, can_moderate: true };
      return route.fulfill({ json: { status: 'SUCCESS', detail: { conversations: [own, ...CONVERSATIONS] } } });
    }
    if (p === '/api/communication/talk/messages' && url.searchParams.get('token') === TOKEN) {
      return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [] } } });
    }
    return route.fallback();
  });
  await page.route('**/api/communication/calls/*/ring', (route) => route.fulfill({ json: { ringing: ['robin'] } }));
  // A microphone that is only a tone: no device, no prompt.
  await page.addInitScript(() => {
    const media = navigator.mediaDevices ?? ({} as MediaDevices);
    media.getUserMedia = async () => {
      const context = new AudioContext();
      const tone = context.createOscillator();
      const out = context.createMediaStreamDestination();
      tone.connect(out);
      tone.start();
      return out.stream;
    };
    if (!navigator.mediaDevices) Object.defineProperty(navigator, 'mediaDevices', { value: media });
  });
  // The gateway's signalling socket: it says the other parent is in the call, then relays nothing back.
  const signals: Signals = { sockets: 0, sent: [] };
  await page.routeWebSocket('**/api/communication/calls/ws**', (ws) => {
    signals.sockets += 1;
    ws.onMessage((raw) => { signals.sent.push(JSON.parse(String(raw)) as Record<string, unknown>); });
    ws.send(JSON.stringify({
      type: 'welcome', self: { id: 'me-1', username: 'parent', name: 'Parent', muted: false, camera: false },
      peers: [{ id: 'robin-1', username: 'robin', name: 'Robin', muted: false, camera: false }],
      ice_servers: [], room: TOKEN, name: 'Private chat', private: true,
    }));
  });
  return signals;
}

async function openPrivateChat(page: Page): Promise<void> {
  await page.goto('/family');
  await page.getByRole('button', { name: /Private chat/ }).first().click();
  await expect(page.getByTestId('private-lock')).toBeVisible();
}

async function looksRight(page: Page, info: TestInfo, name: string, testId: string): Promise<void> {
  await settle(page);
  const panel = page.getByTestId('call-overlay');
  await expectFits(page, panel);
  await expectNoHorizontalOverflow(page);
  await expectTapTargets(page.getByTestId(testId));
  expect(await screenFindings(page, panel, name)).toEqual([]);
  await shot(page, info, name);
}

test.describe('private chat calls', () => {
  test.describe.configure({ timeout: 120_000 });

  test('a locked private chat asks to unlock before calling, and never connects', async ({ page }, info) => {
    const signals = await setUp(page);
    await openPrivateChat(page);
    await page.getByTestId('chat-call').click();
    const locked = page.getByTestId('call-locked');
    await expect(locked).toBeVisible();
    await expect(page.getByTestId('call-overlay')).toContainText('Unlock to call');
    await expect(locked).toContainText('Unlock your private chat first');
    await looksRight(page, info, 'private-call-locked', 'call-locked');
    expect(signals.sockets).toBe(0);

    await page.getByTestId('call-open-private-chat').click();
    await expect(page.getByTestId('call-overlay')).toHaveCount(0);
    await expect(page.getByTestId('private-lock')).toBeVisible();
  });

  test('a peer that never proves itself gets the call ended as unverified', async ({ page }, info) => {
    const signals = await setUp(page);
    await openPrivateChat(page);
    await page.getByTestId('private-password').fill(PASSWORD);
    await page.getByTestId('private-unlock').click();
    await expect(page.getByTestId('private-feed')).toBeVisible();

    await page.getByTestId('chat-call').click();
    await expect(page.getByTestId('call-private-status')).toContainText('Checking this call with your private chat key');
    // This device said hello with a fresh nonce, and sent its offer: never a key.
    await expect.poll(() => signals.sent.some((m) => m.type === 'auth')).toBe(true);
    const hello = signals.sent.find((m) => m.type === 'auth')!.auth as Record<string, unknown>;
    expect(Object.keys(hello).sort()).toEqual(['nonce', 'step', 'v']);

    const unverified = page.getByTestId('call-unverified');
    await expect(unverified).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('call-overlay')).toContainText('Call could not be verified');
    await expect(unverified).toContainText('This call could not be verified');
    await looksRight(page, info, 'private-call-unverified', 'call-unverified');
  });
});
