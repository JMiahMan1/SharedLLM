import { test, expect as baseExpect, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, CONVERSATIONS } from '../fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload, type Allowed } from '../fixtures/pageAudit';
import { deriveKey, encryptBytes, makeVerifier, randomBytes, sealMessage, toBase64, PBKDF2_ITERATIONS } from '../../src/lib/privateChatCrypto';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * The parents' private chat on every form factor: starting one (the create
 * sheet), the lock screen, a wrong password, the unlocked chat with an
 * encrypted photo and the Nudge, the photo viewer, locking again when the
 * app goes to the background, changing the password, and the phone-lock
 * unlock (with a stand-in for the Android plugin). Every screen gets the shared audit (nothing past
 * its card or dialog, no clipped text, nothing off the screen, 44px targets on
 * phones). Hermetic: the server is mocked, the ciphertext is real.
 */

const PASSWORD = 'a long shared sentence';
const TOKEN = 'roomprivate1';
const SEC = Math.floor(Date.now() / 1000);
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

interface Run { mocks: HomeMocks; errors: string[]; found: string[] }

interface Server {
  chats: Array<Record<string, unknown>>;
  changes?: Array<Record<string, unknown>>;
  messages: Array<Record<string, unknown>>;
  blobs: Record<string, Buffer>;
  posted: string[];
  created: Array<Record<string, unknown>>;
  nudges: number;
}

async function serve(page: Page, server: Server): Promise<void> {
  await page.route(/\/api\/(private-chat\/|communication\/talk\/(conversations|messages)(\?|$))/, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (p === '/api/private-chat/status') {
      return route.fulfill({ json: { eligible: true, reason: null, chats: server.chats, min_iterations: PBKDF2_ITERATIONS,
        partners: [{ username: 'robin', display_name: 'Robin' }] } });
    }
    if (p === '/api/private-chat/chats' && req.method() === 'POST') {
      const body = req.postDataJSON() as Record<string, unknown>;
      server.created.push(body);
      const chat = { token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'parent',
        created_at: SEC, version: 2, kdf: { name: 'PBKDF2-SHA256', ...(body.kdf as object) }, wrapped_key: body.wrapped_key, verifier: body.verifier };
      server.chats.push(chat);
      return route.fulfill({ json: chat });
    }
    if (p === `/api/private-chat/chats/${TOKEN}` && req.method() === 'GET') {
      return route.fulfill({ json: server.chats[0] });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/password` && req.method() === 'POST') {
      const body = req.postDataJSON() as Record<string, unknown>;
      (server.changes ??= []).push(body);
      const kdf = body.kdf as Record<string, unknown>;
      server.chats[0] = { ...server.chats[0], version: 2, kdf: { name: 'PBKDF2-SHA256', ...kdf }, wrapped_key: body.wrapped_key };
      return route.fulfill({ json: { ...server.chats[0], notified: ['robin'] } });
    }
    const blob = /^\/api\/private-chat\/chats\/[^/]+\/blobs\/([0-9a-f]+)$/.exec(p);
    if (blob) {
      return route.fulfill({ body: server.blobs[blob[1]], contentType: 'application/octet-stream', headers: { 'cache-control': 'no-store' } });
    }
    if (/\/nudge$/.test(p)) {
      server.nudges += 1;
      return route.fulfill({ json: { nudged: ['robin'], next_in: 300 } });
    }
    if (p === '/api/communication/talk/conversations') {
      const own = server.chats.length
        ? [{ token: TOKEN, display_name: 'Private chat', type: 2, private: true, last_message: 'Encrypted message',
          last_message_actor: 'Parent', last_message_actor_id: 'parent', last_message_timestamp: SEC - 30, last_activity: SEC - 30,
          unread_messages: 0, last_common_read: 2, last_read: 3, can_moderate: true }]
        : [];
      return route.fulfill({ json: { status: 'SUCCESS', detail: { conversations: [...own, ...CONVERSATIONS] } } });
    }
    if (p === '/api/communication/talk/messages' && req.method() === 'GET' && url.searchParams.get('token') === TOKEN) {
      return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: server.messages } } });
    }
    if (p === '/api/communication/talk/messages' && req.method() === 'POST') {
      server.posted.push(String((req.postDataJSON() as { message?: string }).message));
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Chat message sent.' } });
    }
    return route.fallback();
  });
}

/** A chat as the first version made it (the one already in use): its key is the password's. */
async function v1Chat(password: string): Promise<{ key: CryptoKey; salt: string; iterations: number; verifier: { iv: string; ct: string } }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(password, kdf);
  return { key, ...kdf, verifier: await makeVerifier(key) };
}

async function v1Server(): Promise<{ server: Server; key: CryptoKey }> {
  const secrets = await v1Chat(PASSWORD);
  const server: Server = {
    chats: [{ token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin', created_at: SEC - 86400,
      version: 1, kdf: { name: 'PBKDF2-SHA256', salt: secrets.salt, iterations: secrets.iterations }, verifier: secrets.verifier }],
    messages: [{ id: 1, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 600,
      message_type: 'comment', message: await sealMessage(secrets.key, { t: 'text', text: 'Sent before any change' }) }],
    blobs: {}, posted: [], created: [], nudges: 0, changes: [],
  };
  return { server, key: secrets.key };
}

async function unlockWith(page: Page, password: string): Promise<void> {
  await page.getByTestId('private-password').fill(password);
  await page.getByTestId('private-unlock').click();
}

async function open(page: Page, server: Server): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await serve(page, server);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) errors.push(m.text().slice(0, 400));
  });
  await page.goto('/family');
  await expect(page.getByText('Camping trip planning').first()).toBeVisible();
  return { mocks, errors, found: [] };
}

/** The open thread is full screen below lg, but still inside the panel it covers: measure it against the screen, not the panel. */
async function overPanel(page: Page, where: RegExp): Promise<Allowed[]> {
  const fixed = await page.getByTestId('chat-thread').evaluate((el) => getComputedStyle(el).position === 'fixed');
  if (!fixed) return [];
  const panel = (await page.getByTestId('chat-panel').boundingBox())!;
  const edges = `outside ${Math.round(panel.x)}..${Math.round(panel.x + panel.width)})`;
  return [{ where, finding: new RegExp(`outside its card: .* ${edges.replace(/[.)]/g, '\\$&')}$`), why: 'the full-screen thread measured against the panel it covers' }];
}

async function check(page: Page, info: TestInfo, run: Run, where: string, scope: Locator, allowed: Allowed[] = []): Promise<void> {
  await settle(page);
  run.found.push(...await screenFindings(page, scope, where, allowed));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

function done(run: Run) {
  expect(run.found, run.found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

test.describe('private chat on every form factor', () => {
  test.describe.configure({ timeout: 120_000 });

  test('start one: the create sheet, then the new chat already unlocked, then locked', async ({ page }, info) => {
    const server: Server = { chats: [], messages: [], blobs: {}, posted: [], created: [], nudges: 0 };
    const run = await open(page, server);

    await page.getByTestId('chat-new').click();
    await page.getByTestId('new-private-chat-open').click();
    const sheet = page.getByTestId('new-private-chat');
    await expect(sheet).toBeVisible();
    await check(page, info, run, 'private create', page.getByRole('dialog').last());

    await sheet.getByTestId('private-new-password').fill(PASSWORD);
    await sheet.getByTestId('private-confirm-password').fill(`${PASSWORD}!`);
    await expect(sheet.getByText('The two passwords do not match.')).toBeVisible();
    await expect(sheet.getByTestId('private-create')).toBeDisabled();
    await sheet.getByTestId('private-confirm-password').fill(PASSWORD);
    await sheet.getByTestId('private-understood').check();
    await sheet.getByTestId('private-create').click();
    await expect(sheet).toHaveCount(0);

    // Only public parameters went to the server: the wrapped key, its salt and stretch, the verifier and the proof. Never the password.
    const [created] = server.created;
    expect(Object.keys(created).sort()).toEqual(['auth', 'kdf', 'partner', 'verifier', 'version', 'wrapped_key']);
    expect((created.kdf as { iterations: number }).iterations).toBe(PBKDF2_ITERATIONS);
    expect(JSON.stringify(created)).not.toContain(PASSWORD);

    // The creator's key is held already: the chat opens unlocked, and a message goes out sealed.
    await expect(page.getByTestId('private-chat')).toBeVisible();
    await page.getByTestId('private-input').fill('Date night on Friday?');
    await page.getByTestId('private-send').click();
    await expect.poll(() => server.posted.length).toBe(1);
    expect(server.posted[0]).toContain('```jarvis-envelope');
    expect(server.posted[0]).not.toContain('Date night');
    await check(page, info, run, 'private new chat', page.getByTestId('chat-thread'), await overPanel(page, /^private new chat$/));

    await page.getByTestId('private-lock-now').click();
    await expect(page.getByTestId('private-lock')).toBeVisible();
    done(run);
  });

  test('open one: list, lock screen, wrong password, unlocked with a photo and Nudge, viewer, lock on background', async ({ page }, info) => {
    const secrets = await v1Chat(PASSWORD);
    const sealedPhoto = await encryptBytes(secrets.key, new Uint8Array(PNG));
    const blobId = 'ab'.repeat(16);
    const message = async (id: number, actor: string, name: string, text: string, ago: number) => ({
      id, token: TOKEN, actor_type: 'users', actor_id: actor, actor_display_name: name, timestamp: SEC - ago, message_type: 'comment',
      message: await sealMessage(secrets.key, { t: 'text', text }),
    });
    const server: Server = {
      chats: [{ token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin',
        created_at: SEC - 86400, kdf: { name: 'PBKDF2-SHA256', salt: secrets.salt, iterations: secrets.iterations }, verifier: secrets.verifier }],
      messages: [
        await message(1, 'robin', 'Robin', 'Thank you for this morning. A long note so the bubble wraps on a small phone and stays inside it.', 3600),
        { id: 2, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 1800, message_type: 'comment',
          message: await sealMessage(secrets.key, { t: 'image', blob: blobId, mime: 'image/png', name: 'us.png' }) },
        await message(3, 'parent', 'Parent', 'Dinner at seven, just us?', 30),
      ],
      blobs: { [blobId]: Buffer.from(sealedPhoto) },
      posted: [], created: [], nudges: 0,
    };
    const run = await open(page, server);

    // The list: a lock, and never a preview.
    await expect(page.getByTestId(`private-lock-${TOKEN}`)).toBeVisible();
    const row = page.getByRole('button', { name: /Private chat/ }).first();
    await expect(row).toContainText('Encrypted message');
    await check(page, info, run, 'private list', page.locator('main'));

    await row.click();
    const lock = page.getByTestId('private-lock');
    await expect(lock).toBeVisible();
    await expect(page.getByTestId('private-feed')).toHaveCount(0);
    await check(page, info, run, 'private lock screen', page.getByTestId('chat-thread'), await overPanel(page, /^private lock screen$/));

    await page.getByTestId('private-password').fill('not the password');
    await page.getByTestId('private-unlock').click();
    await expect(page.getByTestId('private-wrong')).toBeVisible();
    await check(page, info, run, 'private wrong password', page.getByTestId('chat-thread'), await overPanel(page, /^private wrong password$/));

    await page.getByTestId('private-password').fill(PASSWORD);
    await page.getByTestId('private-unlock').click();
    const feed = page.getByTestId('private-feed');
    await expect(feed.getByText('Dinner at seven, just us?')).toBeVisible();
    await expect(feed.getByTestId('private-picture')).toBeVisible();
    // The picture is shown from an in-memory object URL, and cannot be dragged or saved from a menu.
    const picture = feed.getByTestId('private-picture');
    expect(await picture.getAttribute('src')).toMatch(/^blob:/);
    expect(await picture.getAttribute('draggable')).toBe('false');
    await expect(page.getByTestId('private-nudge')).toBeVisible();
    await check(page, info, run, 'private unlocked', page.getByTestId('chat-thread'), await overPanel(page, /^private unlocked$/));

    await page.getByTestId('private-nudge').click();
    await expect.poll(() => server.nudges).toBe(1);

    await feed.getByRole('button', { name: 'View picture' }).click();
    const viewer = page.getByRole('dialog', { name: 'us.png' });
    await expect(viewer).toBeVisible();
    await check(page, info, run, 'private picture viewer', viewer);
    await viewer.getByRole('button', { name: 'Close picture' }).click();
    await expect(viewer).toHaveCount(0);

    // The app or tab going to the background locks it, and nothing decrypted stays on the page.
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.getByTestId('private-lock')).toBeVisible();
    await expect(page.getByText('Dinner at seven, just us?')).toHaveCount(0);
    expect(await page.locator('img[src^="blob:"]').count()).toBe(0);
    done(run);
  });

  test('change the password: the dialog, a wrong current password, then the history still opens with the new one', async ({ page }, info) => {
    const { server } = await v1Server();
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    const feed = page.getByTestId('private-feed');
    await expect(feed.getByText('Sent before any change')).toBeVisible();

    await page.getByTestId('private-security-open').click();
    const menu = page.getByTestId('private-security');
    await expect(menu).toBeVisible();
    // A browser has no phone lock to offer.
    await expect(menu.getByTestId('private-phone-lock-toggle')).toHaveCount(0);
    await check(page, info, run, 'private security menu', page.getByRole('dialog').last());

    await menu.getByTestId('private-change-password-open').click();
    const dialog = page.getByTestId('private-change-password');
    await expect(dialog).toBeVisible();
    const NEXT = 'a brand new shared sentence';
    await dialog.getByTestId('private-current-password').fill('not the password at all');
    await dialog.getByTestId('private-next-password').fill(NEXT);
    await dialog.getByTestId('private-next-confirm').fill(`${NEXT}?`);
    await expect(dialog.getByText('The two passwords do not match.')).toBeVisible();
    await expect(dialog.getByTestId('private-change-submit')).toBeDisabled();
    await dialog.getByTestId('private-next-confirm').fill(NEXT);
    await dialog.getByTestId('private-change-understood').check();
    await check(page, info, run, 'private change password', page.getByRole('dialog').last());

    // A wrong current password is caught on the device: nothing is sent.
    await dialog.getByTestId('private-change-submit').click();
    await expect(dialog.getByTestId('private-change-error')).toContainText('not the current password');
    expect(server.changes).toEqual([]);
    await check(page, info, run, 'private change password wrong', page.getByRole('dialog').last());

    await dialog.getByTestId('private-current-password').fill(PASSWORD);
    await dialog.getByTestId('private-change-submit').click();
    await expect(dialog).toHaveCount(0);
    expect(server.changes).toHaveLength(1);
    const [change] = server.changes!;
    expect(change.verifier).toEqual(server.chats[0].verifier);
    expect(change.auth).toBeUndefined();
    expect(JSON.stringify(change)).not.toContain(NEXT);
    expect(JSON.stringify(change)).not.toContain(PASSWORD);
    // The person who changed it stays unlocked.
    await expect(feed.getByText('Sent before any change')).toBeVisible();

    // Locked again: the old password no longer opens it, the new one opens the old messages.
    await page.getByTestId('private-lock-now').click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByTestId('private-wrong')).toBeVisible();
    await unlockWith(page, NEXT);
    await expect(page.getByTestId('private-feed').getByText('Sent before any change')).toBeVisible();
    done(run);
  });

  test('unlock with the phone lock: turn it on with the password, then open the chat with it', async ({ page }, info) => {
    // A stand-in for the Android plugin (the dev server only): it keeps what the page gives it.
    await page.addInitScript(() => {
      const stored: Record<string, string> = {};
      (window as unknown as { __jarvisDeviceUnlockMock: unknown }).__jarvisDeviceUnlockMock = {
        available: async () => ({ available: true }),
        isEnabled: async ({ token }: { token: string }) => ({ enabled: token in stored }),
        enable: async ({ token, key }: { token: string; key: string }) => {
          stored[token] = key;
          return { enabled: true };
        },
        unlock: async ({ token }: { token: string }) => {
          if (!(token in stored)) throw new Error('Not set up');
          return { key: stored[token] };
        },
        disable: async ({ token }: { token: string }) => {
          delete stored[token];
          return { enabled: false };
        },
      };
    });
    const { server } = await v1Server();
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByTestId('private-feed').getByText('Sent before any change')).toBeVisible();

    await page.getByTestId('private-security-open').click();
    const toggle = page.getByTestId('private-phone-lock-toggle');
    await expect(toggle).toHaveAttribute('aria-checked', 'false');
    await check(page, info, run, 'private security phone', page.getByRole('dialog').last());
    await toggle.click();
    const sheet = page.getByTestId('private-phone-lock');
    await expect(sheet).toBeVisible();
    await sheet.getByTestId('private-phone-password').fill('wrong wrong wrong');
    await sheet.getByTestId('private-phone-submit').click();
    await expect(sheet.getByTestId('private-phone-error')).toContainText('not the password');
    await check(page, info, run, 'private phone lock setup', page.getByRole('dialog').last());
    await sheet.getByTestId('private-phone-password').fill(PASSWORD);
    await sheet.getByTestId('private-phone-submit').click();
    await expect(page.getByTestId('private-phone-lock-toggle')).toHaveAttribute('aria-checked', 'true');
    await page.getByTestId('private-security').getByRole('button', { name: 'Close' }).last().click();

    // The first lock after turning it on asks the phone by itself (once per opening of the chat)...
    await page.getByTestId('private-lock-now').click();
    await expect(page.getByTestId('private-feed').getByText('Sent before any change')).toBeVisible();
    // ...after that it waits to be asked.
    await page.getByTestId('private-lock-now').click();
    const unlock = page.getByTestId('private-phone-unlock');
    await expect(unlock).toBeVisible();
    await expect(page.getByTestId('private-use-password')).toBeVisible();
    await check(page, info, run, 'private phone unlock', page.getByTestId('chat-thread'), await overPanel(page, /^private phone unlock$/));
    await unlock.click();
    await expect(page.getByTestId('private-feed').getByText('Sent before any change')).toBeVisible();

    // "Use password instead" always works.
    await page.getByTestId('private-lock-now').click();
    await page.getByTestId('private-use-password').click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByTestId('private-feed')).toBeVisible();
    done(run);
  });

  test('a child account: no private chat anywhere, and never a private-chat request', async ({ page }, info) => {
    const asked: string[] = [];
    page.on('request', (req) => {
      if (/\/api\/private-chat\//.test(new URL(req.url()).pathname)) asked.push(req.url());
    });
    await quietHotReload(page);
    const mocks = await mockHome(page, 'member');
    await mockHealth(page);
    await mockBible(page);
    await mockFamilyPages(page);
    const run: Run = { mocks, errors: [], found: [] };
    page.on('pageerror', (e) => run.errors.push(e.message));
    await page.goto('/family');
    await expect(page.getByText('Camping trip planning').first()).toBeVisible();
    await expect(page.locator('[data-testid^="private-lock"]')).toHaveCount(0);
    await check(page, info, run, 'child chat list', page.locator('main'));

    await page.getByTestId('chat-new').click();
    const sheet = page.getByRole('dialog').last();
    await expect(sheet).toBeVisible();
    await expect(page.getByTestId('new-private-chat-open')).toHaveCount(0);
    await expect(sheet.getByText(/private/i)).toHaveCount(0);
    await check(page, info, run, 'child new chat', sheet);
    expect(asked).toEqual([]);
    done(run);
  });
});
