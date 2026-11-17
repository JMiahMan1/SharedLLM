import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect as baseExpect, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, CONVERSATIONS } from '../fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload, type Allowed } from '../fixtures/pageAudit';
import { PBKDF2_ITERATIONS, sealMessage, unlockKey, type ChatKeyInfo } from '../../src/lib/privateChatCrypto';

const expect = baseExpect.configure({ timeout: 30_000 });

/**
 * Backing up, exporting, opening and restoring the parents' private chat, on
 * every form factor. The backup file is a real one the server's own encrypter
 * made of a fake chat (tests/unit/fixtures/private_chat_backup_files.json), so
 * the app opens real ciphertext with the chat password. The server is mocked;
 * the checks are that nothing readable is ever sent, the keepsake shows the
 * chat, a wrong password opens nothing, and every screen passes the shared
 * audit (nothing past its card, no clipped text, 44px targets on phones).
 */

interface Fixture {
  password: string;
  file: string;
  expect: { token: string; members: string[]; texts: string[]; blobs: string[] };
}
const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(readFileSync(resolve(HERE, '../../../../tests/unit/fixtures/private_chat_backup_files.json'), 'utf8')) as Record<'v2', Fixture>;
const VECTORS = JSON.parse(readFileSync(resolve(HERE, '../../../../tests/unit/fixtures/private_chat_backup_vectors.json'), 'utf8')) as {
  v2_chat: Record<string, unknown> & { kdf: { salt: string; iterations: number }; wrapped_key: { iv: string; ct: string }; verifier: { iv: string; ct: string } };
};
const PASSWORD = FIXTURE.v2.password;
const TOKEN = FIXTURE.v2.expect.token;
const BACKUP = Buffer.from(FIXTURE.v2.file, 'base64');
const BACKUP_NAME = 'private-chat-2026-11-16.jarvis-backup';
const SEC = Math.floor(Date.now() / 1000);

interface Server {
  chats: Array<Record<string, unknown>>;
  messages: Array<Record<string, unknown>>;
  exports: number;
  begins: Array<{ registry: Record<string, unknown>; merge: boolean }>;
  needsMerge: boolean;
  blobs: Record<string, number>;
  records: Array<Record<string, unknown>>;
  finished: number;
}

interface Run { mocks: HomeMocks; errors: string[]; found: string[] }

function chatInfo(): Record<string, unknown> {
  const v = VECTORS.v2_chat;
  return {
    token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin', created_at: SEC - 86400,
    beloved: false, storage: 'store', version: 2, kdf: { name: 'PBKDF2-SHA256', ...v.kdf }, wrapped_key: v.wrapped_key, verifier: v.verifier,
    backup_key: { alg: 'X25519', public_key: (v.backup_key as { public_key: string }).public_key, key_id: 'vectorx', created_at: 1_700_000_000 },
  };
}

async function serve(page: Page, server: Server): Promise<void> {
  await page.route(/\/api\/(private-chat\/|communication\/talk\/(conversations|messages|read)(\?|$|\/))/, async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/api/private-chat/status') {
      return route.fulfill({ json: { eligible: true, reason: null, chats: server.chats, min_iterations: PBKDF2_ITERATIONS,
        partners: [{ username: 'robin', display_name: 'Robin' }] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}` && req.method() === 'GET') return route.fulfill({ json: server.chats[0] ?? chatInfo() });
    if (p === `/api/private-chat/chats/${TOKEN}/backup-key`) return route.fulfill({ status: 409, json: { detail: 'This chat already has a backup key.' } });
    if (p === `/api/private-chat/chats/${TOKEN}/export`) {
      server.exports += 1;
      return route.fulfill({ body: BACKUP, contentType: 'application/octet-stream',
        headers: { 'x-backup-filename': BACKUP_NAME, 'content-disposition': `attachment; filename="${BACKUP_NAME}"`, 'cache-control': 'no-store' } });
    }
    if (p === '/api/private-chat/restore' && req.method() === 'POST') {
      const body = req.postDataJSON() as { registry: Record<string, unknown>; merge: boolean };
      server.begins.push(body);
      if (server.needsMerge && !body.merge) {
        return route.fulfill({ status: 409, json: { detail: 'This chat already has messages here.', needs_merge: true, counts: { messages: 2 } } });
      }
      const created = !server.chats.length;
      if (created) server.chats.push(chatInfo());
      return route.fulfill({ json: { ...chatInfo(), created, merge: !created } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/restore/have`) return route.fulfill({ json: { blobs: [FIXTURE.v2.expect.blobs[0]] } });
    const blob = new RegExp(`^/api/private-chat/chats/${TOKEN}/restore/blobs/([0-9a-f]{32})$`).exec(p);
    if (blob && req.method() === 'PUT') {
      server.blobs[blob[1]] = (req.postDataBuffer() ?? Buffer.alloc(0)).length;
      return route.fulfill({ json: { id: blob[1], result: 'added' } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/restore/records` && req.method() === 'POST') {
      const batch = req.postDataJSON() as Record<string, unknown>;
      server.records.push(batch);
      const n = Array.isArray(batch.messages) ? batch.messages.length : 0;
      return route.fulfill({ json: { token: TOKEN, result: { messages: { added: n, same: 0, kept: 0 } } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/restore/finish`) {
      server.finished += 1;
      return route.fulfill({ json: { token: TOKEN, counts: { messages: 6 } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/messages` && req.method() === 'GET') {
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { messages: server.messages } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/messages/wait`) {
      await new Promise((r) => setTimeout(r, 1500));
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { messages: [] } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/read`) return route.fulfill({ json: { status: 'SUCCESS', detail: { last_read: 1 } } });
    if (p === `/api/private-chat/chats/${TOKEN}/marks`) return route.fulfill({ json: { marks: [] } });
    if (p === `/api/private-chat/chats/${TOKEN}/gallery`) return route.fulfill({ json: { entries: [] } });
    if (p === '/api/communication/talk/conversations') {
      const own = server.chats.length
        ? [{ token: TOKEN, display_name: 'Private chat', type: 2, private: true, last_message: 'Encrypted message', last_message_actor: 'Robin',
          last_message_actor_id: 'robin', last_message_timestamp: SEC - 30, last_activity: SEC - 30, unread_messages: 0, last_common_read: 1,
          last_read: 1, can_moderate: true, storage: 'store' }]
        : [];
      return route.fulfill({ json: { status: 'SUCCESS', detail: { conversations: [...own, ...CONVERSATIONS] } } });
    }
    return route.fallback();
  });
}

async function newServer(withChat: boolean): Promise<Server> {
  const server: Server = { chats: [], messages: [], exports: 0, begins: [], needsMerge: false, blobs: {}, records: [], finished: 0 };
  if (withChat) {
    const info = chatInfo();
    server.chats.push(info);
    const key = (await unlockKey(PASSWORD, info as unknown as ChatKeyInfo))!;
    server.messages.push({ id: 7, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 60,
      message_type: 'comment', message: await sealMessage(key, { t: 'text', text: 'A message here now' }) });
    server.needsMerge = true;
  }
  return server;
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

async function unlockChat(page: Page): Promise<void> {
  await page.getByRole('button', { name: /Private chat/ }).first().click();
  await page.getByTestId('private-password').fill(PASSWORD);
  await page.getByTestId('private-unlock').click();
  await expect(page.getByTestId('private-feed').getByText('A message here now')).toBeVisible();
  await page.getByTestId('private-security-open').click();
  await expect(page.getByTestId('private-security')).toBeVisible();
}

/** Every request body this page sent while `fn` ran, as text. */
async function bodiesDuring(page: Page, fn: () => Promise<void>): Promise<string> {
  const bodies: string[] = [];
  const listen = (req: import('@playwright/test').Request) => {
    const body = req.postDataBuffer();
    if (body) bodies.push(body.toString('utf8'));
  };
  page.on('request', listen);
  try {
    await fn();
  } finally {
    page.off('request', listen);
  }
  return bodies.join('\n');
}

test.describe('private chat backups on every form factor', () => {
  test.describe.configure({ timeout: 180_000 });

  test('back up and export: the file, and the encrypted page, saved with a date for a name', async ({ page }, info) => {
    const server = await newServer(true);
    const run = await open(page, server);
    await unlockChat(page);
    await page.getByTestId('private-backup-open-export').click();
    const sheet = page.getByTestId('private-backup');
    await expect(sheet).toBeVisible();
    await check(page, info, run, 'backup export', page.getByRole('dialog').last());

    const fileDownload = page.waitForEvent('download');
    await sheet.getByTestId('private-backup-save').click();
    const file = await fileDownload;
    expect(file.suggestedFilename()).toBe(BACKUP_NAME);
    const saved = readFileSync(await file.path());
    expect(saved.equals(BACKUP)).toBe(true);

    const pageDownload = page.waitForEvent('download');
    await sheet.getByTestId('private-backup-page').click();
    const html = readFileSync(await (await pageDownload).path(), 'utf8');
    expect((await pageDownload).suggestedFilename()).toBe('private-chat-2026-11-16.html');
    expect(html).toContain("default-src 'none'");
    for (const text of [...FIXTURE.v2.expect.texts, ...FIXTURE.v2.expect.members, PASSWORD]) expect(html).not.toContain(text);
    expect(server.exports).toBe(2);
    done(run);
  });

  test('open a backup: a wrong password opens nothing, the right one shows the keepsake, then merge it back', async ({ page }, info) => {
    const server = await newServer(true);
    const run = await open(page, server);
    await unlockChat(page);
    await page.getByTestId('private-backup-open-file').click();
    const sheet = page.getByTestId('private-backup-open');
    await expect(sheet).toBeVisible();
    await check(page, info, run, 'backup open choose', page.getByRole('dialog').last());

    await page.getByTestId('private-backup-file').setInputFiles({ name: BACKUP_NAME, mimeType: 'application/octet-stream', buffer: BACKUP });
    await expect(sheet.getByTestId('private-backup-filename')).toHaveText(BACKUP_NAME);
    await sheet.getByTestId('private-backup-password').fill('not the password');
    await sheet.getByTestId('private-backup-unlock').click();
    await expect(sheet.getByTestId('private-backup-open-error')).toHaveText('That password does not open this backup.');
    await expect(page.getByTestId('private-keepsake')).toHaveCount(0);
    await check(page, info, run, 'backup wrong password', page.getByRole('dialog').last());

    await sheet.getByTestId('private-backup-password').fill(PASSWORD);
    await sheet.getByTestId('private-backup-unlock').click();
    const keepsake = page.getByTestId('private-keepsake');
    await expect(keepsake).toBeVisible();
    const body = keepsake.getByTestId('private-keepsake-body');
    await expect(body.getByText('Good morning, love').first()).toBeVisible();
    await expect(body.getByText('Dinner at seven, just us?')).toBeVisible();
    await expect(body.getByText(/Edited/)).toBeVisible();
    await expect(body.getByText('Deleted')).toBeVisible();
    await expect(body.getByTestId('keepsake-picture').first()).toBeVisible();
    expect(await body.getByTestId('keepsake-picture').first().getAttribute('src')).toMatch(/^blob:/);
    await check(page, info, run, 'backup keepsake messages', keepsake);

    await keepsake.getByTestId('private-keepsake-tab-gallery').click();
    await expect(body.getByTestId('keepsake-picture')).toHaveCount(1);
    await check(page, info, run, 'backup keepsake gallery', keepsake);
    await keepsake.getByTestId('private-keepsake-tab-beloved').click();
    await expect(body.getByText('You make every day better')).toBeVisible();
    await expect(body.getByText('Our first date')).toBeVisible();
    await check(page, info, run, 'backup keepsake beloved', keepsake);

    await keepsake.getByTestId('private-keepsake-restore').click();
    const restore = page.getByTestId('private-restore');
    await expect(restore).toBeVisible();
    await check(page, info, run, 'backup restore', page.getByRole('dialog').last());
    const sent = await bodiesDuring(page, async () => {
      await restore.getByTestId('private-restore-start').click();
      await expect(restore.getByTestId('private-restore-merge')).toBeVisible();
      await check(page, info, run, 'backup restore merge', page.getByRole('dialog').last());
      await restore.getByTestId('private-restore-merge-confirm').click();
      await expect(restore).toHaveCount(0);
    });
    expect(server.begins.map((b) => b.merge)).toEqual([false, true]);
    expect(server.begins[0].registry.token).toBe(TOKEN);
    expect(Object.keys(server.blobs).sort()).toEqual(FIXTURE.v2.expect.blobs.slice(1));
    expect(server.finished).toBe(1);
    expect(server.records.flatMap((r) => (r.messages as unknown[]) ?? [])).toHaveLength(6);
    // Nothing readable went to the server: no message text, no Beloved words, no password.
    for (const text of [...FIXTURE.v2.expect.texts, 'You make every day better', 'Our first date', PASSWORD]) expect(sent).not.toContain(text);
    done(run);
  });

  test('a parent with no chat here restores it from the start screen', async ({ page }, info) => {
    const server = await newServer(false);
    const run = await open(page, server);
    await page.getByTestId('chat-new').click();
    await page.getByTestId('new-private-chat-open').click();
    await expect(page.getByTestId('new-private-chat')).toBeVisible();
    await page.getByTestId('private-open-backup').click();
    const sheet = page.getByTestId('private-backup-open');
    await expect(sheet).toBeVisible();
    await check(page, info, run, 'backup start screen', page.getByRole('dialog').last());

    await page.getByTestId('private-backup-file').setInputFiles({ name: BACKUP_NAME, mimeType: 'application/octet-stream', buffer: BACKUP });
    await sheet.getByTestId('private-backup-password').fill(PASSWORD);
    await sheet.getByTestId('private-backup-unlock').click();
    await expect(page.getByTestId('private-keepsake')).toBeVisible();
    await page.getByTestId('private-keepsake-restore').click();
    await page.getByTestId('private-restore-start').click();
    await expect(page.getByTestId('private-restore')).toHaveCount(0);
    expect(server.begins).toHaveLength(1);
    expect(server.begins[0].merge).toBe(false);
    expect(server.finished).toBe(1);
    // The chat is back on the list, locked: it opens with the same password.
    await expect(page.getByTestId('private-lock')).toBeVisible();
    done(run);
  });
});
