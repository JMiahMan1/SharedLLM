import { test, expect as baseExpect, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, CONVERSATIONS } from '../fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload, type Allowed } from '../fixtures/pageAudit';
import JSZip from 'jszip';
import { deriveKey, encryptBytes, makeVerifier, openMessage, openRecord, randomBytes, sealMessage, sealRecord, toBase64, PBKDF2_ITERATIONS } from '../../src/lib/privateChatCrypto';
import { fakeJpeg } from '../../src/test/fakes/fakeJpeg';
import { TINY_WEBM_BASE64 } from '../fixtures/tinyVideo';

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
  uploads?: Buffer[];
  messages: Array<Record<string, unknown>>;
  blobs: Record<string, Buffer>;
  posted: string[];
  /** Talk calls that named the private chat: there must be none, its messages are the gateway's. */
  talk?: string[];
  /** Read markers this device moved, and messages it deleted. */
  read?: number;
  deleted?: number[];
  created: Array<Record<string, unknown>>;
  nudges: number;
  /** Pictures added straight to the gallery: sealed entries, never Talk messages. */
  entries?: Array<{ id: string; at: number; own: boolean; by_name: string; sealed: string }>;
  /** Sealed edits and reactions per message, as the gateway lists them to this parent. */
  marks?: Mark[];
  /** What this device sent: sealed reactions and edits, and the reaction ids it marked seen. */
  reacted?: string[];
  edited?: string[];
  seen?: string[];
  /** The blob ids each sent message said it is made of, and the blobs this device deleted. */
  messageBlobs?: string[][];
  blobsDeleted?: string[];
}

interface Mark {
  message_id: number;
  edit: { at: number; by: string; own: boolean; sealed: string } | null;
  reactions: Array<{ id: string; by: string; by_name: string; own: boolean; at: number; seen: boolean; sealed: string }>;
}

async function serve(page: Page, server: Server): Promise<void> {
  await page.route(/\/api\/(private-chat\/|communication\/talk\/(conversations|messages|read)(\?|$|\/))/, async (route: Route) => {
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
    if (p === `/api/private-chat/chats/${TOKEN}/blobs` && req.method() === 'POST') {
      const body = req.postDataBuffer() ?? Buffer.alloc(0);
      (server.uploads ??= []).push(body);
      const id = (server.uploads.length).toString(16).padStart(32, '0');
      server.blobs[id] = body;
      return route.fulfill({ json: { id, size: body.length } });
    }
    const blob = /^\/api\/private-chat\/chats\/[^/]+\/blobs\/([0-9a-f]+)$/.exec(p);
    if (blob && req.method() === 'DELETE') {
      (server.blobsDeleted ??= []).push(blob[1]);
      delete server.blobs[blob[1]];
      return route.fulfill({ json: { deleted: blob[1] } });
    }
    if (blob && !server.blobs[blob[1]]) {
      return route.fulfill({ status: 404, json: { detail: 'No such picture.' } });
    }
    if (blob) {
      return route.fulfill({ body: server.blobs[blob[1]], contentType: 'application/octet-stream', headers: { 'cache-control': 'no-store' } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/gallery` && req.method() === 'GET') {
      return route.fulfill({ json: { entries: server.entries ?? [] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/gallery` && req.method() === 'POST') {
      const sealed = String((req.postDataJSON() as { sealed?: string }).sealed);
      const entries = (server.entries ??= []);
      const id = (entries.length + 1).toString(16).padStart(32, 'e');
      entries.push({ id, at: Math.floor(Date.now() / 1000), own: true, by_name: 'Parent', sealed });
      return route.fulfill({ json: { id, at: Math.floor(Date.now() / 1000) } });
    }
    const entry = /^\/api\/private-chat\/chats\/[^/]+\/gallery\/([0-9a-f]+)$/.exec(p);
    if (entry && req.method() === 'DELETE') {
      server.entries = (server.entries ?? []).filter((e) => e.id !== entry[1]);
      return route.fulfill({ json: { deleted: entry[1] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/marks` && req.method() === 'GET') {
      return route.fulfill({ json: { marks: server.marks ?? [] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/marks/seen` && req.method() === 'POST') {
      const ids = (req.postDataJSON() as { ids: string[] }).ids;
      (server.seen ??= []).push(...ids);
      for (const mark of server.marks ?? []) for (const r of mark.reactions) if (ids.includes(r.id)) r.seen = true;
      return route.fulfill({ json: { seen: ids } });
    }
    const markOf = (id: number): Mark => {
      const marks = (server.marks ??= []);
      let mark = marks.find((m) => m.message_id === id);
      if (!mark) {
        mark = { message_id: id, edit: null, reactions: [] };
        marks.push(mark);
      }
      return mark;
    };
    const edit = new RegExp(`^/api/private-chat/chats/${TOKEN}/messages/(\\d+)/edit$`).exec(p);
    if (edit && req.method() === 'PUT') {
      const sealed = String((req.postDataJSON() as { sealed?: string }).sealed);
      (server.edited ??= []).push(sealed);
      const mark = markOf(Number(edit[1]));
      mark.edit = { at: Math.floor(Date.now() / 1000), by: 'parent', own: true, sealed };
      return route.fulfill({ json: mark });
    }
    const reactions = new RegExp(`^/api/private-chat/chats/${TOKEN}/messages/(\\d+)/reactions(?:/([0-9a-f]{32}))?$`).exec(p);
    if (reactions && req.method() === 'POST') {
      const sealed = String((req.postDataJSON() as { sealed?: string }).sealed);
      (server.reacted ??= []).push(sealed);
      const mark = markOf(Number(reactions[1]));
      const id = (server.reacted.length).toString(16).padStart(32, 'a');
      mark.reactions.push({ id, by: 'parent', by_name: 'Parent', own: true, at: Math.floor(Date.now() / 1000), seen: true, sealed });
      return route.fulfill({ json: { ...mark, reaction_id: id, notified: true } });
    }
    if (reactions?.[2] && req.method() === 'DELETE') {
      const mark = markOf(Number(reactions[1]));
      mark.reactions = mark.reactions.filter((r) => r.id !== reactions[2]);
      return route.fulfill({ json: mark });
    }
    if (/\/nudge$/.test(p)) {
      server.nudges += 1;
      return route.fulfill({ json: { nudged: ['robin'], next_in: 300 } });
    }
    if (p === '/api/communication/talk/conversations') {
      const own = server.chats.length
        ? [{ token: TOKEN, display_name: 'Private chat', type: 2, private: true, last_message: 'Encrypted message',
          last_message_actor: 'Parent', last_message_actor_id: 'parent', last_message_timestamp: SEC - 30, last_activity: SEC - 30,
          unread_messages: 0, last_common_read: 2, last_read: 3, can_moderate: true, storage: 'store' }]
        : [];
      return route.fulfill({ json: { status: 'SUCCESS', detail: { conversations: [...own, ...CONVERSATIONS] } } });
    }
    // The private chat's messages are the gateway's own (never Talk's): read, sent, waited for and deleted there.
    if (p === `/api/private-chat/chats/${TOKEN}/messages` && req.method() === 'GET') {
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { messages: server.messages } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/messages/wait`) {
      const after = Number(url.searchParams.get('last_id')) || 0;
      const newer = server.messages.filter((m) => Number(m.id) > after);
      if (!newer.length) await new Promise((r) => setTimeout(r, 1500));
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { messages: newer } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/messages` && req.method() === 'POST') {
      const body = req.postDataJSON() as { sealed?: string; blobs?: string[] };
      const posted = String(body.sealed);
      server.posted.push(posted);
      (server.messageBlobs ??= []).push(body.blobs ?? []);
      // Like the gateway: what was sent is in the thread on the next read, with the next id.
      const nextId = Math.max(0, ...server.messages.map((m) => Number(m.id) || 0)) + 1;
      const message = { id: nextId, token: TOKEN, actor_type: 'users', actor_id: 'parent', actor_display_name: 'Parent',
        timestamp: Math.floor(Date.now() / 1000), message_type: 'comment', message: posted, deleted: false };
      server.messages.push(message);
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Sent.', source: 'store', detail: { message } } });
    }
    const removed = new RegExp(`^/api/private-chat/chats/${TOKEN}/messages/(\\d+)$`).exec(p);
    if (removed && req.method() === 'DELETE') {
      const id = Number(removed[1]);
      server.deleted = [...(server.deleted ?? []), id];
      server.messages = server.messages.map((m) => (Number(m.id) === id ? { ...m, deleted: true, message: '', message_type: 'comment_deleted' } : m));
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Message deleted.', source: 'store', detail: { deleted: id } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/read` && req.method() === 'POST') {
      server.read = Math.max(0, ...server.messages.map((m) => Number(m.id) || 0));
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { token: TOKEN, last_read: server.read, unread_messages: 0 } } });
    }
    if (/^\/api\/communication\/talk\/messages/.test(p) && (url.searchParams.get('token') === TOKEN || req.postData()?.includes(TOKEN))) {
      server.talk = [...(server.talk ?? []), `${req.method()} ${p}`];
      return route.fulfill({ status: 500, json: { detail: 'The private chat must not go through Talk' } });
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
    expect(server.talk ?? []).toEqual([]);
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

  test('react, edit and see the other parent\'s reaction play once: the sheet, the burst, the chips, "Edited"', async ({ page }, info) => {
    const secrets = await v1Chat(PASSWORD);
    const blobId = 'cd'.repeat(16);
    const text = async (id: number, actor: string, name: string, body: string, ago: number) => ({
      id, token: TOKEN, actor_type: 'users', actor_id: actor, actor_display_name: name, timestamp: SEC - ago, message_type: 'comment',
      message: await sealMessage(secrets.key, { t: 'text', text: body }),
    });
    const heartId = 'f'.repeat(32);
    const server: Server = {
      chats: [{ token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin',
        created_at: SEC - 86400, kdf: { name: 'PBKDF2-SHA256', salt: secrets.salt, iterations: secrets.iterations }, verifier: secrets.verifier }],
      messages: [
        await text(1, 'robin', 'Robin', 'Made it home. The kids are asleep and I saved you some soup.', 3600),
        { id: 2, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 1800, message_type: 'comment',
          message: await sealMessage(secrets.key, { t: 'image', blob: blobId, mime: 'image/png', name: 'sunset.png' }) },
        await text(3, 'parent', 'Parent', 'Dinner at seven, just us?', 60),
      ],
      blobs: { [blobId]: Buffer.from(await encryptBytes(secrets.key, new Uint8Array(PNG))) },
      posted: [], created: [], nudges: 0,
      // Robin loved my message while I was away: it waits, unseen, to play when I open the chat.
      marks: [{ message_id: 3, edit: null, reactions: [{ id: heartId, by: 'robin', by_name: 'Robin', own: false, at: SEC - 30, seen: false,
        sealed: await sealRecord(secrets.key, { t: 'reaction', m: 3, e: '\u2764\ufe0f' }) }] }],
    };
    const run = await open(page, server);
    // A phone with a home indicator: the sheet must sit clear of it.
    await page.evaluate(() => document.documentElement.style.setProperty('--safe-area-inset-bottom', '34px'));
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    const feed = page.getByTestId('private-feed');
    await expect(feed.getByText('Dinner at seven, just us?')).toBeVisible();

    // The waiting heart plays on open (a running animation over my bubble), then is marked seen.
    const burst = page.getByTestId('reaction-animation');
    await expect(burst).toHaveAttribute('data-kind', 'love');
    expect(await burst.evaluate((el) => el.getAnimations({ subtree: true }).length)).toBeGreaterThan(0);
    await shot(page, info, 'private reaction animation');
    await expect.poll(() => server.seen ?? []).toEqual([heartId]);
    await expect(burst).toHaveCount(0);
    const mine = feed.locator('[data-message-id="3"]').locator('..');
    await expect(mine.getByTestId('private-reaction-chip')).toHaveAttribute('aria-label', /from Robin/);
    // Seen: it does not play again on the next refresh.
    await page.waitForTimeout(5_600);
    await expect(burst).toHaveCount(0);

    // Robin's message: react only (no Edit or Delete for the other parent's words).
    await feed.getByText('Made it home.', { exact: false }).click();
    const sheet = page.getByTestId('private-message-actions');
    await expect(sheet).toBeVisible();
    await expect(sheet.getByTestId('private-message-edit')).toHaveCount(0);
    await expect(sheet.getByTestId('private-message-delete')).toHaveCount(0);
    await check(page, info, run, 'private message sheet partner', sheet);
    await sheet.getByTestId('private-react-like').click();
    await expect(sheet).toHaveCount(0);
    await expect(burst).toHaveAttribute('data-kind', 'like');
    await expect.poll(() => server.reacted?.length ?? 0).toBe(1);
    // Sealed on the device: the emoji never reaches the server, and it opens to this message only.
    expect(server.reacted![0]).toContain('```jarvis-envelope');
    expect(server.reacted![0]).not.toContain('\ud83d\udc4d');
    expect(await openRecord(secrets.key, server.reacted![0])).toEqual({ t: 'reaction', m: 1, e: '\ud83d\udc4d' });
    const theirs = feed.locator('[data-message-id="1"]').locator('..');
    await expect(theirs.getByTestId('private-reaction-chip')).toHaveAttribute('aria-label', /from You/);

    // Tapping the same emoji again takes it back.
    await feed.getByText('Made it home.', { exact: false }).click();
    await expect(sheet.getByTestId('private-react-like')).toHaveAttribute('aria-pressed', 'true');
    await sheet.getByTestId('private-react-like').click();
    await expect(theirs.getByTestId('private-reaction-chip')).toHaveCount(0);

    // My message: every action, each a full-size target clear of the home indicator.
    await feed.getByText('Dinner at seven, just us?').click();
    await expect(sheet).toBeVisible();
    await expect(sheet.getByTestId('private-message-edit')).toBeVisible();
    await expect(sheet.getByTestId('private-message-delete')).toBeVisible();
    await page.waitForTimeout(250); // the sheet's slide-up
    const viewport = page.viewportSize()!;
    for (const button of await sheet.locator('button:visible').all()) {
      const label = (await button.getAttribute('aria-label')) ?? (await button.innerText());
      if (label === 'Close') continue; // the backdrop behind the panel
      const box = (await button.boundingBox())!;
      expect(box.height, `${label} is a 44px target`).toBeGreaterThanOrEqual(44);
      expect(box.width, `${label} is a 44px target`).toBeGreaterThanOrEqual(44);
      expect(box.y + box.height, `${label} clears the home indicator`).toBeLessThanOrEqual(viewport.height - 34);
      expect(box.x, `${label} is on screen`).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, `${label} is on screen`).toBeLessThanOrEqual(viewport.width);
    }
    await check(page, info, run, 'private message sheet', sheet);

    // Edit: the composer takes the text, the new version is sealed, and the bubble says it was edited.
    await sheet.getByTestId('private-message-edit').click();
    await expect(page.getByTestId('private-editing')).toBeVisible();
    await expect(page.getByTestId('private-input')).toHaveValue('Dinner at seven, just us?');
    await check(page, info, run, 'private editing', page.getByTestId('chat-thread'), await overPanel(page, /^private editing$/));
    await page.getByTestId('private-input').fill('Dinner at eight, just us?');
    await page.getByTestId('private-edit-save').click();
    await expect(feed.getByText('Dinner at eight, just us?')).toBeVisible();
    await expect(page.getByTestId('private-editing')).toHaveCount(0);
    expect(server.posted).toEqual([]); // an edit is not a new message
    expect(server.edited![0]).not.toContain('Dinner at eight');
    expect(await openRecord(secrets.key, server.edited![0])).toEqual({ t: 'edit', m: 3, text: 'Dinner at eight, just us?' });
    await expect(mine.getByTestId('private-edited')).toBeVisible();
    await feed.getByText('Dinner at eight, just us?').click();
    await expect(sheet.getByTestId('private-edited-at')).toContainText('Edited at');
    await sheet.getByTestId('private-message-cancel').click();
    await check(page, info, run, 'private edited and reacted', page.getByTestId('chat-thread'), await overPanel(page, /^private edited and reacted$/));

    // A reaction from Robin while the chat is open and on screen plays at once, on the next refresh.
    server.marks!.push({ message_id: 1, edit: null, reactions: [{ id: 'e'.repeat(32), by: 'robin', by_name: 'Robin', own: false, at: SEC, seen: false,
      sealed: await sealRecord(secrets.key, { t: 'reaction', m: 1, e: '\ud83d\ude4f' }) }] });
    await expect(burst).toHaveAttribute('data-kind', 'pray', { timeout: 12_000 });
    await expect.poll(() => server.seen).toEqual([heartId, 'e'.repeat(32)]);

    // A picture takes reactions in its viewer.
    await feed.getByRole('button', { name: 'View picture' }).click();
    const viewer = page.getByRole('dialog', { name: 'sunset.png' });
    await expect(viewer.getByTestId('private-reaction-row')).toBeVisible();
    await check(page, info, run, 'private picture viewer reactions', viewer);
    await viewer.getByTestId('private-react-laugh').click();
    await expect(viewer).toHaveCount(0);
    await expect(burst).toHaveAttribute('data-kind', 'laugh');
    await expect.poll(async () => openRecord(secrets.key, server.reacted!.at(-1))).toEqual({ t: 'reaction', m: 2, e: '\ud83d\ude02' });

    // With reduced motion the reaction only fades.
    await expect(burst).toHaveCount(0);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await feed.getByText('Made it home.', { exact: false }).click();
    await sheet.getByTestId('private-react-wow').click();
    await expect(burst).toHaveAttribute('data-kind', 'calm');
    await expect(burst).toHaveCount(0);

    // Delete my message for both of us: it goes to the gateway's store, and leaves the thread.
    await feed.getByText('Dinner at eight, just us?').click();
    await sheet.getByTestId('private-message-delete').click();
    await expect.poll(() => server.deleted).toEqual([3]);
    await expect(feed.getByText('Dinner at eight, just us?')).toHaveCount(0);
    // Nothing about this chat ever went through Talk.
    expect(server.talk ?? []).toEqual([]);
    done(run);
  });

  test('a link in a message opens in the browser and does not open the message sheet', async ({ page, context }, info) => {
    const { server, key } = await v1Server();
    server.messages.push({ id: 2, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 60,
      message_type: 'comment', message: await sealMessage(key, { t: 'text', text: 'The cabin is https://example.com/cabin?week=2, look!' }) });
    await context.route('https://example.com/**', (route) => route.fulfill({ body: 'cabin', contentType: 'text/plain' }));
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    const link = page.getByTestId('private-link');
    await expect(link).toHaveAttribute('href', 'https://example.com/cabin?week=2');
    await expect(link).toHaveText('https://example.com/cabin?week=2');
    await check(page, info, run, 'private chat link', page.getByTestId('chat-thread'), [
      ...await overPanel(page, /private chat link/),
      // WCAG 2.5.8 exempts a link inside a sentence: its size is the text's.
      { where: /private chat link/, finding: /under 44px: https:\/\/example\.com\/cabin/, why: 'an inline link in message text' },
    ]);
    const popup = context.waitForEvent('page');
    await link.click();
    const opened = await popup;
    await opened.waitForURL('https://example.com/cabin?week=2');
    await opened.close();
    // The tap went to the link only: the message's own sheet stayed closed.
    await expect(page.getByRole('dialog')).toHaveCount(0);
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

  test('lock timing: kept open for a minute after leaving, locked at once by default', async ({ page }, info) => {
    const { server } = await v1Server();
    const run = await open(page, server);
    const privateRow = page.getByRole('button', { name: /Private chat/ }).first();
    const leaveAndComeBack = async () => {
      const back = page.getByRole('button', { name: 'Back to chats' });
      if (await back.isVisible()) await back.click();
      await page.getByText('Camping trip planning').first().click();
      if (await back.isVisible()) await back.click();
      await privateRow.click();
    };

    await privateRow.click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByTestId('private-feed').getByText('Sent before any change')).toBeVisible();
    await page.getByTestId('private-security-open').click();
    const menu = page.getByTestId('private-security');
    await expect(menu.getByTestId('private-lock-after-leave')).toHaveAttribute('aria-checked', 'true');
    await menu.getByTestId('private-lock-after-1m').click();
    await expect(menu.getByTestId('private-lock-after-1m')).toHaveAttribute('aria-checked', 'true');
    await check(page, info, run, 'private lock setting', page.getByRole('dialog').last());
    await menu.getByRole('button', { name: 'Close' }).last().click();

    // Leaving and coming back within the minute: still open, no password asked.
    await leaveAndComeBack();
    await expect(page.getByTestId('private-feed').getByText('Sent before any change')).toBeVisible();
    await expect(page.getByTestId('private-lock')).toHaveCount(0);

    // Back to the default: leaving locks at once.
    await page.getByTestId('private-security-open').click();
    await page.getByTestId('private-lock-after-leave').click();
    await page.getByTestId('private-security').getByRole('button', { name: 'Close' }).last().click();
    await leaveAndComeBack();
    await expect(page.getByTestId('private-lock')).toBeVisible();
    done(run);
  });

  test('message text in notifications: off by default, turned on with the password (phone stand-in)', async ({ page }, info) => {
    await page.addInitScript(() => {
      const kept: Record<string, string> = {};
      (window as unknown as { __jarvisPrivatePreviewMock: unknown }).__jarvisPrivatePreviewMock = {
        available: async () => ({ available: true }),
        isEnabled: async ({ token }: { token: string }) => ({ enabled: token in kept }),
        enable: async ({ token, key }: { token: string; key: string }) => {
          kept[token] = key;
          return { enabled: true };
        },
        disable: async ({ token }: { token: string }) => {
          delete kept[token];
          return { enabled: false };
        },
      };
    });
    const { server } = await v1Server();
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByTestId('private-feed')).toBeVisible();
    await page.getByTestId('private-security-open').click();
    const toggle = page.getByTestId('private-preview-toggle');
    await expect(toggle).toHaveAttribute('aria-checked', 'false');
    await toggle.click();
    const sheet = page.getByTestId('private-preview-setup');
    await expect(sheet.getByText('Less private')).toBeVisible();
    await sheet.getByTestId('private-preview-password').fill('wrong wrong wrong');
    await sheet.getByTestId('private-preview-submit').click();
    await expect(sheet.getByTestId('private-preview-error')).toContainText('not the password');
    await check(page, info, run, 'private preview setup', page.getByRole('dialog').last());
    await sheet.getByTestId('private-preview-password').fill(PASSWORD);
    await sheet.getByTestId('private-preview-submit').click();
    await expect(page.getByTestId('private-preview-toggle')).toHaveAttribute('aria-checked', 'true');
    await page.getByTestId('private-preview-toggle').click();
    await expect(page.getByTestId('private-preview-toggle')).toHaveAttribute('aria-checked', 'false');
    done(run);
  });

  test('a pasted or dropped photo is encrypted before it leaves the device, and a non-photo is refused', async ({ page }) => {
    const { server } = await v1Server();
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByTestId('private-feed')).toBeVisible();
    const png = [...PNG];

    await page.getByTestId('private-input').evaluate((el, bytes) => {
      const data = new DataTransfer();
      data.items.add(new File([new Uint8Array(bytes)], 'pasted.png', { type: 'image/png' }));
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    }, png);
    await expect.poll(() => server.posted.length).toBe(1);

    await page.getByTestId('private-feed').evaluate((el, bytes) => {
      const data = new DataTransfer();
      data.items.add(new File([new Uint8Array(bytes)], 'dropped.png', { type: 'image/png' }));
      el.dispatchEvent(new DragEvent('dragover', { dataTransfer: data, bubbles: true, cancelable: true }));
      el.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
    }, png);
    await expect.poll(() => server.posted.length).toBe(2);

    // Uploads are ciphertext (no PNG signature, IV + tag longer), messages are sealed envelopes naming no file.
    expect(server.uploads).toHaveLength(2);
    for (const upload of server.uploads!) {
      expect(upload.length).toBe(PNG.length + 12 + 16);
      expect(upload.includes(PNG.subarray(0, 8))).toBe(false);
    }
    for (const posted of server.posted) {
      expect(posted).toContain('```jarvis-envelope');
      expect(posted).not.toContain('pasted.png');
      expect(posted).not.toContain('dropped.png');
    }

    // A file that is not a photo is refused, and nothing is uploaded.
    await page.getByTestId('private-feed').evaluate((el) => {
      const data = new DataTransfer();
      data.items.add(new File(['secret notes'], 'notes.txt', { type: 'text/plain' }));
      el.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
    });
    await expect(page.getByText('Only photos and videos can be sent in a private chat')).toBeVisible();
    expect(server.uploads).toHaveLength(2);
    expect(page.url()).toContain('/family');
    done(run);
  });

  test('the in-app camera: front and back, take, send encrypted, nothing left behind', async ({ page }, info) => {
    // A stand-in camera: a canvas stream, recording what was asked for and whether it was stopped.
    await page.addInitScript(() => {
      const log = { facing: [] as string[], stopped: 0 };
      (window as unknown as { __cameraLog: typeof log }).__cameraLog = log;
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: {
          getUserMedia: async (constraints: MediaStreamConstraints) => {
            const video = constraints.video as { facingMode?: { ideal?: string } };
            log.facing.push(String(video?.facingMode?.ideal));
            const canvas = document.createElement('canvas');
            canvas.width = 640;
            canvas.height = 480;
            const ctx = canvas.getContext('2d')!;
            const draw = () => {
              ctx.fillStyle = log.facing.length % 2 ? '#c2410c' : '#0369a1';
              ctx.fillRect(0, 0, 640, 480);
              ctx.fillStyle = '#fff';
              ctx.fillRect(200, 140, 240, 200);
            };
            draw();
            const timer = setInterval(draw, 100);
            const stream = canvas.captureStream(10);
            for (const track of stream.getTracks()) {
              const stop = track.stop.bind(track);
              track.stop = () => {
                log.stopped += 1;
                clearInterval(timer);
                stop();
              };
            }
            return stream;
          },
        },
      });
    });
    const { server } = await v1Server();
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByTestId('private-feed')).toBeVisible();
    // Never the system camera: no capture input anywhere on the page.
    expect(await page.locator('input[capture]').count()).toBe(0);

    await page.getByTestId('private-camera-open').click();
    const camera = page.getByTestId('private-camera');
    await expect(camera.getByTestId('private-camera-shutter')).toBeEnabled();
    await check(page, info, run, 'private camera', camera);
    await camera.getByTestId('private-camera-flip').click();
    await expect(camera.getByTestId('private-camera-shutter')).toBeEnabled();
    // The back camera first, then the front (the dev build's strict mode may ask twice for each).
    const asked = await page.evaluate(() => (window as unknown as { __cameraLog: { facing: string[] } }).__cameraLog.facing);
    expect(asked.filter((f, i) => f !== asked[i - 1])).toEqual(['environment', 'user']);

    await camera.getByTestId('private-camera-shutter').click();
    await expect(camera.getByTestId('private-camera-still')).toBeVisible();
    await check(page, info, run, 'private camera taken', camera);
    await camera.getByTestId('private-camera-send').click();
    await expect(camera).toHaveCount(0);
    await expect.poll(() => server.posted.length).toBe(1);
    expect(server.posted[0]).toContain('```jarvis-envelope');
    const [upload] = server.uploads!;
    // Ciphertext: not a JPEG (FF D8) on the wire.
    expect(upload[0] === 0xff && upload[1] === 0xd8).toBe(false);
    expect(await page.evaluate(() => (window as unknown as { __cameraLog: { stopped: number } }).__cameraLog.stopped)).toBeGreaterThanOrEqual(2);
    done(run);
  });

  test('the encrypted gallery: every photo by day with its time, opened full screen, commented on by both', async ({ page }, info) => {
    const { server, key } = await v1Server();
    const photoA = 'cd'.repeat(16);
    const photoB = 'ef'.repeat(16);
    server.blobs[photoA] = Buffer.from(await encryptBytes(key, new Uint8Array(PNG)));
    server.blobs[photoB] = Buffer.from(await encryptBytes(key, new Uint8Array(PNG)));
    server.messages.push(
      // An older photo with no time inside: the gallery uses when Talk got it.
      { id: 2, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 3 * 86400,
        message_type: 'comment', message: await sealMessage(key, { t: 'image', blob: photoA, mime: 'image/png' }) },
      { id: 3, token: TOKEN, actor_type: 'users', actor_id: 'parent', actor_display_name: 'Parent', timestamp: SEC - 120,
        message_type: 'comment', message: await sealMessage(key, { t: 'image', blob: photoB, mime: 'image/png', sent_at: SEC - 120, taken_at: SEC - 600 }) },
      { id: 4, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 60,
        message_type: 'comment', message: await sealMessage(key, { t: 'comment', blob: photoB, text: 'What a view' }) },
    );
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    // In the thread a comment shows as a reply to a photo.
    const bubble = page.getByTestId('private-comment-bubble');
    await expect(bubble).toContainText('On a photo');
    await expect(bubble).toContainText('What a view');

    await page.getByTestId('private-gallery-open').click();
    const gallery = page.getByTestId('private-gallery');
    await expect(gallery.getByTestId('private-gallery-tile')).toHaveCount(2);
    await expect(gallery.getByTestId('private-gallery-count')).toHaveText('2 photos');
    await expect(gallery.getByRole('heading', { name: 'Today' })).toBeVisible();
    await expect(gallery.locator('img[src^="blob:"]')).toHaveCount(2);
    await check(page, info, run, 'private gallery', gallery);

    await gallery.getByTestId('private-gallery-tile').first().click();
    const viewer = page.getByTestId('private-gallery-viewer');
    await expect(viewer.getByTestId('private-gallery-stamp')).toContainText('Sent');
    await expect(viewer.getByTestId('private-gallery-comment')).toContainText('What a view');
    expect(await viewer.locator('img').first().getAttribute('draggable')).toBe('false');
    await viewer.getByTestId('private-gallery-comment-input').fill('Best weekend');
    await viewer.getByTestId('private-gallery-comment-send').click();
    await expect.poll(() => server.posted.length).toBe(1);
    // The comment is a sealed message: neither its text nor which photo it is about is readable on the server.
    expect(server.posted[0]).toContain('```jarvis-envelope');
    expect(server.posted[0]).not.toContain('Best weekend');
    expect(server.posted[0]).not.toContain(photoB);
    await expect(viewer.getByTestId('private-gallery-comment')).toHaveCount(2);
    await check(page, info, run, 'private gallery viewer', viewer);
    await viewer.getByRole('button', { name: 'Back to the gallery' }).click();
    await gallery.getByRole('button', { name: 'Close gallery' }).click();
    await expect(gallery).toHaveCount(0);

    // Tapping a comment in the thread opens its photo.
    await page.getByTestId('private-comment-bubble').first().click();
    await expect(page.getByTestId('private-gallery-viewer')).toBeVisible();
    await page.getByRole('button', { name: 'Back to the gallery' }).click();
    await page.getByRole('button', { name: 'Close gallery' }).click();

    // Locking wipes it: no decrypted picture is left on the page.
    await page.getByTestId('private-lock-now').click();
    expect(await page.locator('img[src^="blob:"]').count()).toBe(0);
    done(run);
  });

  test('add pictures to the gallery without sending them, and import a Between export: by the day taken, never in the chat', async ({ page }, info) => {
    const { server, key } = await v1Server();
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByText('Sent before any change')).toBeVisible();
    await page.getByTestId('private-gallery-open').click();
    const gallery = page.getByTestId('private-gallery');
    await expect(gallery.getByText(/No photos yet/)).toBeVisible();
    await gallery.getByTestId('private-gallery-add').click();
    const sheet = page.getByTestId('private-gallery-import');
    await expect(sheet.getByRole('heading', { name: 'Import from Between' })).toBeVisible();
    await check(page, info, run, 'private gallery add pictures', sheet);

    // Two pictures from the phone (made-up EXIF: an old date, a made-up camera and place) and the same one again.
    const first = Buffer.from(fakeJpeg({ dateTimeOriginal: '2016:05:12 14:30:00', make: 'Acme', model: 'Pocket 7', gps: { lat: 10.5, lon: 20.25 } }, 1));
    const second = Buffer.from(fakeJpeg(undefined, 2));
    const chooser = page.waitForEvent('filechooser');
    await sheet.getByTestId('private-import-phone').click();
    await (await chooser).setFiles([
      { name: 'IMG_0001.jpg', mimeType: 'image/jpeg', buffer: first },
      { name: 'IMG_20180704_211509.jpg', mimeType: 'image/jpeg', buffer: second },
      { name: 'IMG_0001 copy.jpg', mimeType: 'image/jpeg', buffer: first },
    ]);
    const result = sheet.getByTestId('private-import-result');
    await expect(result).toContainText('2 added, 1 already in the gallery');
    await expect(sheet.getByTestId('private-import-originals')).toContainText('still on your phone');
    await check(page, info, run, 'private gallery add pictures done', sheet);

    // Encrypted before it left: the blobs are not the pictures, and the entries say nothing readable.
    expect(server.uploads?.length).toBe(2);
    expect(server.uploads?.some((u) => u.includes(first.subarray(0, 64)))).toBe(false);
    expect(server.entries?.length).toBe(2);
    for (const e of server.entries ?? []) {
      expect(e.sealed).toContain('```jarvis-envelope');
      for (const secret of ['IMG_', 'Acme', '10.5', '2016']) expect(e.sealed).not.toContain(secret);
    }
    const opened = await openMessage(key, server.entries![0].sealed);
    // The first of the two identical files (in name order) is the one kept.
    expect(opened).toMatchObject({ t: 'added', name: 'IMG_0001 copy.jpg', camera: 'Acme Pocket 7', taken_from: 'exif', source: 'phone' });
    // Never a chat message: nothing was posted to Talk.
    expect(server.posted).toEqual([]);

    // A Between backup zip: the picture already in is skipped, the new one lands on its folder's date, the video is left out.
    const zip = new JSZip();
    zip.file('Between/photos/IMG_0001.jpg', first);
    zip.file('Between/photos/2012-12-25/photo_3.jpg', Buffer.from(fakeJpeg(undefined, 3)));
    zip.file('Between/videos/clip.mp4', Buffer.from([0, 0, 0, 24]));
    const between = page.waitForEvent('filechooser');
    await sheet.getByTestId('private-import-between').click();
    await (await between).setFiles({ name: 'between-backup.zip', mimeType: 'application/zip', buffer: Buffer.from(await zip.generateAsync({ type: 'uint8array' })) });
    await expect(result).toContainText('1 added, 1 already in the gallery');
    await expect(result).toContainText('1 video was left out');
    await sheet.getByRole('button', { name: 'Close' }).click();
    await expect(sheet).toHaveCount(0);

    // In the gallery by the day each was taken, years apart, for both parents.
    await expect(gallery.getByTestId('private-gallery-count')).toHaveText('3 photos');
    await expect(gallery.getByTestId('private-gallery-tile')).toHaveCount(3);
    await expect(gallery.getByRole('heading', { name: /2016/ })).toBeVisible();
    await expect(gallery.getByRole('heading', { name: /2012/ })).toBeVisible();
    await expect(gallery.locator('img[src^="blob:"]')).toHaveCount(3);
    await check(page, info, run, 'private gallery with imported pictures', gallery);

    await gallery.getByRole('button', { name: /taken .*2016/ }).click();
    const viewer = page.getByTestId('private-gallery-viewer');
    await expect(viewer.getByTestId('private-gallery-stamp')).toContainText('Taken');
    await expect(viewer.getByTestId('private-gallery-details')).toContainText('IMG_0001 copy.jpg');
    await expect(viewer.getByTestId('private-gallery-details')).toContainText('Acme Pocket 7');
    await expect(viewer.getByTestId('private-gallery-place')).toContainText('10.5000, 20.2500');
    // Comments work as on a sent picture: a sealed chat message naming the picture.
    await viewer.getByTestId('private-gallery-comment-input').fill('Our first trip');
    await viewer.getByTestId('private-gallery-comment-send').click();
    await expect.poll(() => server.posted.length).toBe(1);
    expect(server.posted[0]).not.toContain('Our first trip');
    await expect(viewer.getByTestId('private-gallery-comment')).toContainText('Our first trip');
    await check(page, info, run, 'private gallery imported picture', viewer);

    // Removing it removes the entry and its encrypted picture.
    await viewer.getByTestId('private-gallery-delete').click();
    await expect(gallery.getByTestId('private-gallery-tile')).toHaveCount(2);
    expect(server.entries?.length).toBe(2);
    await gallery.getByRole('button', { name: 'Close gallery' }).click();

    // The thread shows the comment, never the added pictures themselves.
    await expect(page.getByTestId('private-picture')).toHaveCount(0);
    await page.getByTestId('private-lock-now').click();
    expect(await page.locator('img[src^="blob:"]').count()).toBe(0);
    done(run);
  });

  test('a video clip from the phone: sent encrypted in pieces with its poster, shown with its length, played, in the gallery, deleted', async ({ page }, info) => {
    const { server, key } = await v1Server();
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    const feed = page.getByTestId('private-feed');
    await expect(feed.getByText('Sent before any change')).toBeVisible();

    // Picked through the same chooser as photos (on Android, the WebView's file chooser), which offers videos too.
    const accept = await page.getByTestId('private-file-input').getAttribute('accept');
    expect(accept).toContain('video/mp4');
    expect(accept).toContain('video/*');
    expect(await page.locator('input[capture]').count()).toBe(0);
    const clip = Buffer.from(TINY_WEBM_BASE64, 'base64');
    const chooser = page.waitForEvent('filechooser');
    await page.getByTestId('private-pick').click();
    await (await chooser).setFiles({ name: 'kids-in-the-garden.webm', mimeType: 'video/webm', buffer: clip });
    await expect.poll(() => server.posted.length).toBe(1);

    // The server got opaque blobs (the poster and one piece) and a sealed message naming none of it readably.
    expect(server.uploads).toHaveLength(2);
    for (const upload of server.uploads!) {
      expect(upload.includes(clip.subarray(0, 4)), 'no WebM signature on the wire').toBe(false);
      expect(upload[0] === 0xff && upload[1] === 0xd8, 'no JPEG signature on the wire').toBe(false);
    }
    const posted = server.posted[0];
    expect(posted).toContain('```jarvis-envelope');
    for (const secret of ['kids-in-the-garden', 'video', 'webm']) expect(posted).not.toContain(secret);
    const opened = await openMessage(key, posted);
    expect(opened).toMatchObject({ t: 'video', mime: 'video/webm', size: clip.length, width: 64, height: 36, name: 'kids-in-the-garden.webm', chunk_bytes: 4 * 1024 * 1024 });
    if (opened?.t !== 'video') throw new Error('not a video');
    expect(opened.duration).toBeGreaterThan(1.5);
    expect(opened.duration).toBeLessThan(2.5);
    expect(opened.chunks).toHaveLength(1);
    expect(opened.sent_at).toBeGreaterThan(0);
    // The message names its blobs for the server, so deleting it deletes them too.
    expect(server.messageBlobs![0].sort()).toEqual([...opened.chunks, opened.poster].sort());
    // A piece is the clip plus IV and tag; the poster is a picture of its own.
    expect(server.blobs[opened.chunks[0]].length).toBe(clip.length + 28);

    // The bubble: the decrypted poster, a play button and the length.
    const bubble = feed.getByTestId('private-video');
    await expect(bubble).toBeVisible();
    await expect(bubble).toHaveAttribute('aria-label', 'Play video, 0:02');
    await expect(bubble.getByTestId('private-video-duration')).toHaveText('0:02');
    expect(await bubble.getByTestId('private-video-poster').getAttribute('src')).toMatch(/^blob:/);
    expect(await bubble.getByTestId('private-video-poster').getAttribute('draggable')).toBe('false');
    await check(page, info, run, 'private video bubble', page.getByTestId('chat-thread'), await overPanel(page, /^private video bubble$/));

    // The player: decrypted into memory, played from a blob: URL, with reactions and delete.
    await bubble.click();
    const player = page.getByTestId('private-video-player');
    const playing = player.getByTestId('private-video-playing');
    await expect(playing).toBeVisible();
    const src = (await playing.getAttribute('src'))!;
    expect(src).toMatch(/^blob:/);
    expect(await playing.getAttribute('controlslist')).toContain('nodownload');
    await expect.poll(() => playing.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
    expect(await playing.evaluate((v: HTMLVideoElement) => [v.videoWidth, v.videoHeight])).toEqual([64, 36]);
    await expect(player.getByTestId('private-reaction-row')).toBeVisible();
    await check(page, info, run, 'private video player', player);
    await player.getByRole('button', { name: 'Close video' }).click();
    await expect(player).toHaveCount(0);
    // Closing let go of the decrypted clip at once.
    expect(await page.evaluate(async (url) => fetch(url).then(() => 'alive', () => 'revoked'), src)).toBe('revoked');

    // A reaction from the player, sealed like any other.
    await bubble.click();
    await player.getByTestId('private-react-love').click();
    await expect(player).toHaveCount(0);
    await expect.poll(async () => openRecord(key, server.reacted?.at(-1))).toEqual({ t: 'reaction', m: 2, e: '❤️' });

    // The gallery shows the clip beside the photos, with its length, and plays it.
    await page.getByTestId('private-gallery-open').click();
    const gallery = page.getByTestId('private-gallery');
    await expect(gallery.getByTestId('private-gallery-count')).toHaveText('1 video');
    const tile = gallery.getByTestId('private-gallery-tile');
    await expect(tile).toHaveAttribute('aria-label', /^Video from Parent, .*0:02/);
    await expect(tile.getByTestId('private-gallery-video-duration')).toHaveText('0:02');
    await check(page, info, run, 'private gallery with a video', gallery);
    await tile.click();
    const viewer = page.getByTestId('private-gallery-viewer');
    await viewer.getByTestId('private-gallery-video-play').click();
    await expect(viewer.getByTestId('private-gallery-video-playing')).toHaveAttribute('src', /^blob:/);
    await expect(viewer.getByTestId('private-gallery-comment-input')).toHaveAttribute('placeholder', 'Comment on this video');
    await check(page, info, run, 'private gallery video viewer', viewer);
    await viewer.getByRole('button', { name: 'Back to the gallery' }).click();
    await gallery.getByRole('button', { name: 'Close gallery' }).click();

    // Delete for both: the message, then (belt and braces with the server) every piece and the poster.
    await bubble.click();
    await player.getByTestId('private-video-delete').click();
    await expect.poll(() => server.deleted).toEqual([2]);
    await expect.poll(() => (server.blobsDeleted ?? []).slice().sort()).toEqual([...opened.chunks, opened.poster].sort());
    await expect(feed.getByTestId('private-video')).toHaveCount(0);

    // Locking leaves nothing decrypted on the page.
    await page.getByTestId('private-lock-now').click();
    expect(await page.locator('[src^="blob:"]').count()).toBe(0);
    expect(server.talk ?? []).toEqual([]);
    done(run);
  });

  test('a video recorded in the app: the timer, stop, watch it back, send it encrypted; nothing left running', async ({ page }, info) => {
    // A stand-in camera and microphone: a drawn stream and a tone, so Chromium's own MediaRecorder records a real clip.
    await page.addInitScript(() => {
      const log = { stopped: 0, audio: [] as boolean[] };
      (window as unknown as { __recLog: typeof log }).__recLog = log;
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: {
          getUserMedia: async (constraints: MediaStreamConstraints) => {
            log.audio.push(Boolean(constraints.audio));
            const canvas = document.createElement('canvas');
            canvas.width = 320;
            canvas.height = 180;
            const ctx = canvas.getContext('2d')!;
            let frame = 0;
            const draw = () => {
              frame += 1;
              ctx.fillStyle = '#7c3aed';
              ctx.fillRect(0, 0, 320, 180);
              ctx.fillStyle = '#fde68a';
              ctx.fillRect((frame * 8) % 320, 60, 60, 60);
            };
            draw();
            const timer = setInterval(draw, 50);
            const stream = canvas.captureStream(20);
            if (constraints.audio) {
              const audio = new AudioContext();
              const tone = audio.createOscillator();
              const out = audio.createMediaStreamDestination();
              tone.connect(out);
              tone.start();
              for (const track of out.stream.getAudioTracks()) stream.addTrack(track);
            }
            for (const track of stream.getTracks()) {
              const stop = track.stop.bind(track);
              track.stop = () => {
                log.stopped += 1;
                clearInterval(timer);
                stop();
              };
            }
            return stream;
          },
        },
      });
    });
    const { server, key } = await v1Server();
    const run = await open(page, server);
    await page.getByRole('button', { name: /Private chat/ }).first().click();
    await unlockWith(page, PASSWORD);
    await expect(page.getByTestId('private-feed')).toBeVisible();

    // One camera button: photo first, then switch to video.
    await page.getByTestId('private-camera-open').click();
    await page.getByTestId('private-camera').getByTestId('private-mode-video').click();
    const recorder = page.getByTestId('private-recorder');
    await expect(recorder.getByTestId('private-recorder-record')).toBeEnabled();
    await expect(recorder.getByTestId('private-recorder-timer')).toHaveText('0:00 / 3:00');
    await check(page, info, run, 'private recorder', recorder);

    await recorder.getByTestId('private-recorder-record').click();
    const stop = recorder.getByTestId('private-recorder-stop');
    await expect(stop).toBeVisible();
    // The flip is off while recording; the clock runs.
    await expect(recorder.getByTestId('private-recorder-flip')).toBeDisabled();
    await expect(recorder.getByTestId('private-recorder-timer')).toHaveText('0:02 / 3:00', { timeout: 6_000 });
    await check(page, info, run, 'private recorder recording', recorder);
    await stop.click();

    // Watch it back from memory, then send.
    const review = recorder.getByTestId('private-recorder-review');
    await expect(review).toBeVisible();
    expect(await review.getAttribute('src')).toMatch(/^blob:/);
    await expect(recorder.getByTestId('private-recorder-length')).toContainText('0:0');
    await check(page, info, run, 'private recorder review', recorder);
    await recorder.getByTestId('private-recorder-send').click();
    await expect(recorder).toHaveCount(0);
    await expect.poll(() => server.posted.length).toBe(1);

    const opened = await openMessage(key, server.posted[0]);
    expect(opened).toMatchObject({ t: 'video', width: 320, height: 180 });
    if (opened?.t !== 'video') throw new Error('not a video');
    // MP4 where the browser records and plays it, WebM otherwise.
    expect(['video/mp4', 'video/webm']).toContain(opened.mime);
    expect(opened.duration).toBeGreaterThanOrEqual(2);
    expect(opened.duration).toBeLessThan(10);
    expect(opened.taken_at).toBeGreaterThan(0);
    expect(server.uploads).toHaveLength(1 + opened.chunks.length);
    const log = await page.evaluate(() => (window as unknown as { __recLog: { stopped: number; audio: boolean[] } }).__recLog);
    // The photo camera asks for no sound; the recorder asks for the microphone.
    expect(log.audio[0]).toBe(false);
    expect(log.audio).toContain(true);
    // Camera and microphone both stopped.
    expect(log.stopped).toBeGreaterThanOrEqual(2);

    // It plays back for the other parent too: the bubble, then the player.
    const bubble = page.getByTestId('private-feed').getByTestId('private-video');
    await expect(bubble).toBeVisible();
    await bubble.click();
    await expect(page.getByTestId('private-video-playing')).toHaveAttribute('src', /^blob:/);
    await page.getByRole('button', { name: 'Close video' }).click();
    done(run);
  });
});
