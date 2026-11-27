import { test, expect as baseExpect, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, CONVERSATIONS } from '../fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload } from '../fixtures/pageAudit';
import {
  decryptBytes,
  deriveKey,
  encryptBytes,
  makeVerifier,
  openMessage,
  randomBytes,
  sealMessage,
  toBase64,
  PBKDF2_ITERATIONS,
  type PrivatePayload,
} from '../../src/lib/privateChatCrypto';
import { fakeGif } from '../../src/test/fakes/fakeGif';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * The photo editor everywhere a picture is chosen, on every form factor:
 * - the private chat's picker opens it (Edit, Send as is; closing sends nothing), a still and an animated GIF,
 *   and what reaches the server is ciphertext that decrypts to the edited picture;
 * - a gallery picture is edited from its viewer: the edit is a new sealed blob plus a sealed edit record naming the
 *   original, which is never changed; Edit again starts from the original with the recipe; Revert puts the original
 *   back; deleting the picture removes its edits;
 * - a family chat's photo picker opens it and only the edited picture is sent;
 * - all of it clear of the Android bars with 3-button navigation and large text.
 * Hermetic: the server is mocked.
 */

const PASSWORD = 'a long shared sentence';
const TOKEN = 'roomeverywhere1';
const SEC = Math.floor(Date.now() / 1000);
const ORIGINAL = 'f'.repeat(32);

interface Entry {
  id: string;
  at: number;
  own: boolean;
  by_name: string;
  sealed: string;
}

interface Server {
  chats: Array<Record<string, unknown>>;
  messages: Array<Record<string, unknown>>;
  blobs: Record<string, Buffer>;
  uploads: string[];
  blobsDeleted: string[];
  posted: string[];
  entries: Entry[];
  entriesDeleted: string[];
  talkFiles: Array<Record<string, unknown>>;
}

interface Run {
  mocks: HomeMocks;
  errors: string[];
  found: string[];
}

async function serve(page: Page, server: Server): Promise<void> {
  await page.route(/\/api\/(private-chat\/|communication\/talk\/(conversations|file)(\?|$|\/))/, async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/api/private-chat/status') {
      return route.fulfill({ json: { eligible: true, reason: null, chats: server.chats, min_iterations: PBKDF2_ITERATIONS, partners: [{ username: 'robin', display_name: 'Robin' }] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}` && req.method() === 'GET') return route.fulfill({ json: server.chats[0] });
    if (p === `/api/private-chat/chats/${TOKEN}/blobs` && req.method() === 'POST') {
      const body = req.postDataBuffer() ?? Buffer.alloc(0);
      const id = (server.uploads.length + 1).toString(16).padStart(32, '0');
      server.uploads.push(id);
      server.blobs[id] = body;
      return route.fulfill({ json: { id, size: body.length } });
    }
    const blob = /\/blobs\/([0-9a-f]+)$/.exec(p);
    if (blob && req.method() === 'DELETE') {
      server.blobsDeleted.push(blob[1]);
      delete server.blobs[blob[1]];
      return route.fulfill({ json: { deleted: blob[1] } });
    }
    if (blob) return server.blobs[blob[1]] ? route.fulfill({ body: server.blobs[blob[1]], contentType: 'application/octet-stream' }) : route.fulfill({ status: 404, json: {} });
    if (p === `/api/private-chat/chats/${TOKEN}/gallery` && req.method() === 'GET') return route.fulfill({ json: { entries: server.entries } });
    if (p === `/api/private-chat/chats/${TOKEN}/gallery` && req.method() === 'POST') {
      const sealed = String((req.postDataJSON() as { sealed?: string }).sealed);
      const id = (server.entries.length + server.entriesDeleted.length + 1).toString(16).padStart(32, 'e');
      const at = Math.floor(Date.now() / 1000);
      server.entries.push({ id, at, own: true, by_name: 'Parent', sealed });
      return route.fulfill({ json: { id, at } });
    }
    const entry = /\/gallery\/([0-9a-f]+)$/.exec(p);
    if (entry && req.method() === 'DELETE') {
      server.entriesDeleted.push(entry[1]);
      server.entries = server.entries.filter((e) => e.id !== entry[1]);
      return route.fulfill({ json: { deleted: entry[1] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/marks`) return route.fulfill({ json: { marks: [] } });
    if (p === `/api/private-chat/chats/${TOKEN}/reminders`) {
      return route.fulfill({ json: { enabled: true, available: true, delay_s: 21600, question_delay_s: 7200, quiet: '21:30-07:30', daily_max: 2 } });
    }
    if (p === '/api/communication/talk/conversations') {
      const own = [{ token: TOKEN, display_name: 'Private chat', type: 2, private: true, last_message: 'Encrypted message', last_message_actor: 'Parent', last_message_actor_id: 'parent',
        last_message_timestamp: SEC - 30, last_activity: SEC - 30, unread_messages: 0, last_common_read: 1, last_read: 1, can_moderate: true, storage: 'store' }];
      return route.fulfill({ json: { status: 'SUCCESS', detail: { conversations: [...own, ...CONVERSATIONS] } } });
    }
    if (p === '/api/communication/talk/file' && req.method() === 'POST') {
      server.talkFiles.push(req.postDataJSON() as Record<string, unknown>);
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Sent.' } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/messages` && req.method() === 'GET') {
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { messages: server.messages } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/messages/wait`) {
      await new Promise((r) => setTimeout(r, 1500));
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { messages: [] } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/messages` && req.method() === 'POST') {
      const posted = String((req.postDataJSON() as { sealed?: string }).sealed);
      server.posted.push(posted);
      const id = Math.max(0, ...server.messages.map((m) => Number(m.id) || 0)) + 1;
      const message = { id, token: TOKEN, actor_type: 'users', actor_id: 'parent', actor_display_name: 'Parent', timestamp: Math.floor(Date.now() / 1000),
        message_type: 'comment', message: posted, deleted: false };
      server.messages.push(message);
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Sent.', source: 'store', detail: { message } } });
    }
    const removed = new RegExp(`^/api/private-chat/chats/${TOKEN}/messages/(\\d+)$`).exec(p);
    if (removed && req.method() === 'DELETE') {
      const id = Number(removed[1]);
      server.messages = server.messages.map((m) => (Number(m.id) === id ? { ...m, deleted: true, message: '', message_type: 'comment_deleted' } : m));
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Message deleted.', source: 'store', detail: { deleted: id } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/read`) {
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { token: TOKEN, last_read: server.messages.length, unread_messages: 0 } } });
    }
    return route.fallback();
  });
}

/** A gallery record padded as the app pads it (privateGalleryModel.padded): one size for all of them. */
function padded(record: Record<string, unknown>): Record<string, unknown> {
  const bare = Buffer.byteLength(JSON.stringify({ ...record, p: '' }));
  return { ...record, p: ' '.repeat(Math.max(0, 8192 - bare)) };
}

/**
 * A private chat. With `picture`, a picture the parent sent earlier, put in the gallery (the opt-in start, then an
 * add record): the gallery tests edit it.
 */
async function chat(picture?: Buffer): Promise<{ server: Server; key: CryptoKey }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(PASSWORD, kdf);
  const server: Server = {
    chats: [{ token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin', created_at: SEC - 86400, version: 1,
      kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key) }],
    messages: [{ id: 1, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 900, message_type: 'comment',
      message: await sealMessage(key, { t: 'text', text: 'Show me the garden' }) }],
    blobs: {},
    uploads: [],
    blobsDeleted: [],
    posted: [],
    entries: [],
    entriesDeleted: [],
    talkFiles: [],
  };
  if (picture) {
    server.blobs[ORIGINAL] = Buffer.from(await encryptBytes(key, new Uint8Array(picture)));
    server.messages.push({ id: 2, token: TOKEN, actor_type: 'users', actor_id: 'parent', actor_display_name: 'Parent', timestamp: SEC - 600, message_type: 'comment',
      message: await sealMessage(key, { t: 'image', blob: ORIGINAL, mime: 'image/png', name: 'garden.png', sent_at: SEC - 600 }) });
    const seal = async (record: Record<string, unknown>) => sealMessage(key, padded(record) as unknown as PrivatePayload);
    server.entries.push(
      { id: '1'.repeat(32), at: SEC - 500, own: true, by_name: 'Parent', sealed: await seal({ t: 'gallery_start', kept: [ORIGINAL] }) },
      { id: '2'.repeat(32), at: SEC - 400, own: true, by_name: 'Parent', sealed: await seal({ t: 'gallery', op: 'add', blobs: [ORIGINAL] }) },
    );
  }
  return { server, key };
}

async function open(page: Page, server: Server): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await serve(page, server);
  const run: Run = { mocks, errors: [], found: [] };
  page.on('pageerror', (e) => run.errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) run.errors.push(m.text().slice(0, 400));
  });
  await page.goto('/family');
  await expect(page.getByText('Camping trip planning').first()).toBeVisible();
  return run;
}

async function check(page: Page, info: TestInfo, run: Run, where: string, scope: Locator): Promise<void> {
  await settle(page);
  run.found.push(...(await screenFindings(page, scope, where)));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

function done(run: Run) {
  expect(run.found, run.found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

async function unlock(page: Page): Promise<void> {
  await page.getByRole('button', { name: /Private chat/ }).first().click();
  await page.getByTestId('private-password').fill(PASSWORD);
  await page.getByTestId('private-unlock').click();
  await expect(page.getByTestId('private-feed').getByText('Show me the garden')).toBeVisible();
}

/** A drawn picture: red on the left half, blue on the right, as PNG bytes (made by the page's own canvas). */
async function drawnPng(page: Page): Promise<Buffer> {
  const b64 = await page.evaluate(() => {
    const c = document.createElement('canvas');
    c.width = 200;
    c.height = 150;
    const ctx = c.getContext('2d')!;
    ctx.fillStyle = '#d92626';
    ctx.fillRect(0, 0, 100, 150);
    ctx.fillStyle = '#2563eb';
    ctx.fillRect(100, 0, 100, 150);
    return c.toDataURL('image/png').split(',')[1];
  });
  return Buffer.from(b64, 'base64');
}

/** Decode a picture's bytes in the page: its size, frame count (ImageDecoder where there is one) and the colour at a point. */
async function readPicture(page: Page, bytes: Uint8Array, mime: string, at: [number, number]): Promise<{ width: number; height: number; rgb: number[]; frames: number }> {
  return page.evaluate(
    async ({ b64, mime, at }) => {
      const bin = atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) arr[i] = bin.charCodeAt(i);
      let frames = 1;
      if ('ImageDecoder' in window) {
        const decoder = new ImageDecoder({ data: arr, type: mime });
        await decoder.tracks.ready;
        await decoder.completed;
        frames = decoder.tracks.selectedTrack?.frameCount ?? 1;
        decoder.close();
      }
      const bitmap = await createImageBitmap(new Blob([arr], { type: mime }));
      const c = document.createElement('canvas');
      c.width = bitmap.width;
      c.height = bitmap.height;
      const ctx = c.getContext('2d')!;
      ctx.drawImage(bitmap, 0, 0);
      const d = ctx.getImageData(Math.floor(at[0] * c.width), Math.floor(at[1] * c.height), 1, 1).data;
      return { width: c.width, height: c.height, rgb: [d[0], d[1], d[2]], frames };
    },
    { b64: Buffer.from(bytes).toString('base64'), mime, at },
  );
}

const grey = (rgb: number[]) => Math.max(...rgb) - Math.min(...rgb) < 12;

/** The gallery's sealed entries, opened. */
async function records(key: CryptoKey, server: Server): Promise<Array<{ entry: Entry; payload: PrivatePayload | null }>> {
  return Promise.all(server.entries.map(async (entry) => ({ entry, payload: await openMessage(key, entry.sealed) })));
}

async function edits(key: CryptoKey, server: Server) {
  return (await records(key, server)).flatMap(({ entry, payload }) => (payload?.t === 'gallery_edit' ? [{ entry, payload }] : []));
}

test.describe('the photo editor everywhere a picture is chosen', () => {
  test.describe.configure({ timeout: 150_000 });

  test('the private chat picker: Edit and send encrypted, Send as is, and closing sends nothing', async ({ page }, info) => {
    const { server, key } = await chat();
    const run = await open(page, server);
    const png = await drawnPng(page);
    await unlock(page);

    const pick = async (file: { name: string; mimeType: string; buffer: Buffer }) => {
      const chooser = page.waitForEvent('filechooser');
      await page.getByTestId('private-pick').click();
      await (await chooser).setFiles(file);
      const editor = page.getByTestId('photo-editor');
      await expect(editor.getByTestId('photo-editor-tools')).toBeVisible();
      return editor;
    };

    // Picked, then the editor closed: nothing is sent.
    let editor = await pick({ name: 'garden.png', mimeType: 'image/png', buffer: png });
    await editor.getByTestId('photo-editor-close').click();
    await expect(editor).toHaveCount(0);
    await page.waitForTimeout(300);
    expect(server.posted).toHaveLength(0);
    expect(server.uploads).toHaveLength(0);

    // Edit: Noir, then Send.
    editor = await pick({ name: 'garden.png', mimeType: 'image/png', buffer: png });
    await expect(editor.getByTestId('photo-editor-as-is')).toHaveText('Send as is');
    await check(page, info, run, 'editor from the private picker', editor);
    await editor.getByRole('button', { name: 'Noir filter' }).click();
    await editor.getByTestId('photo-editor-done').click();
    await expect(editor).toHaveCount(0);
    await expect.poll(() => server.posted.length).toBe(1);
    const sealed = server.blobs[server.uploads[0]];
    expect(sealed.subarray(0, 4).equals(png.subarray(0, 4)), 'no PNG signature on the wire').toBe(false);
    expect(sealed[0] === 0xff && sealed[1] === 0xd8, 'no JPEG signature on the wire').toBe(false);
    expect(server.posted[0]).not.toContain('garden');
    const sent = await openMessage(key, server.posted[0]);
    expect(sent).toMatchObject({ t: 'image', mime: 'image/jpeg', name: 'garden-edited.jpg' });
    const plain = await decryptBytes(key, new Uint8Array(sealed));
    const picture = await readPicture(page, plain, 'image/jpeg', [0.25, 0.5]);
    expect([picture.width, picture.height]).toEqual([200, 150]);
    expect(grey(picture.rgb), `Noir turned the red half grey: ${picture.rgb}`).toBe(true);
    await expect(page.getByTestId('private-feed').locator('img[src^="blob:"]')).toHaveCount(1);

    // Send as is: the picked file, byte for byte.
    editor = await pick({ name: 'garden.png', mimeType: 'image/png', buffer: png });
    await editor.getByTestId('photo-editor-as-is').click();
    await expect(editor).toHaveCount(0);
    await expect.poll(() => server.posted.length).toBe(2);
    expect(await openMessage(key, server.posted[1])).toMatchObject({ t: 'image', mime: 'image/png', name: 'garden.png' });
    expect(Buffer.from(await decryptBytes(key, new Uint8Array(server.blobs[server.uploads[1]]))).equals(png)).toBe(true);
    done(run);
  });

  test('an animated GIF picked from the phone is edited frame by frame and stays animated', async ({ page }) => {
    test.skip(test.info().project.use.browserName === 'webkit', 'Safari has no ImageDecoder: the editor edits the first frame there, which the unit tests cover');
    const { server, key } = await chat();
    const run = await open(page, server);
    await unlock(page);
    const gif = Buffer.from(fakeGif(40, 30, [[220, 38, 38], [37, 99, 235], [22, 163, 74]], 15));
    const chooser = page.waitForEvent('filechooser');
    await page.getByTestId('private-pick').click();
    await (await chooser).setFiles({ name: 'dance.gif', mimeType: 'image/gif', buffer: gif });
    const editor = page.getByTestId('photo-editor');
    await expect(editor.getByTestId('photo-editor-notice')).toContainText('all 3 frames');
    await editor.getByRole('button', { name: 'Mono filter' }).click();
    await editor.getByTestId('photo-editor-done').click();
    await expect(editor).toHaveCount(0);
    await expect.poll(() => server.posted.length).toBe(1);
    expect(await openMessage(key, server.posted[0])).toMatchObject({ t: 'image', mime: 'image/gif', name: 'dance-edited.gif' });
    const plain = await decryptBytes(key, new Uint8Array(server.blobs[server.uploads[0]]));
    const picture = await readPicture(page, plain, 'image/gif', [0.5, 0.5]);
    expect(picture.frames).toBe(3);
    expect([picture.width, picture.height]).toEqual([40, 30]);
    expect(grey(picture.rgb)).toBe(true);
    done(run);
  });

  test('a gallery picture: edited beside its original, edited again from the original, reverted, and deleted with its edits', async ({ page }, info) => {
    const png = Buffer.from(await (async () => {
      // The same drawn picture, made before the page opens (the chat's history holds it).
      const p = await page.context().newPage();
      await p.goto('about:blank');
      const b = await drawnPng(p);
      await p.close();
      return b;
    })());
    const { server, key } = await chat(png);
    const originalSealed = Buffer.from(server.blobs[ORIGINAL]);
    const run = await open(page, server);
    await unlock(page);
    await page.getByTestId('private-gallery-open').click();
    const gallery = page.getByTestId('private-gallery');
    const tile = gallery.locator(`[data-testid="private-gallery-tile"][data-blob="${ORIGINAL}"]`);
    await expect(tile).toBeVisible();
    await tile.click();
    const viewer = page.getByTestId('private-gallery-viewer');
    await expect(viewer.getByTestId('private-gallery-photo')).toBeVisible();
    await expect(viewer.getByTestId('private-gallery-edited')).toHaveCount(0);
    await check(page, info, run, 'gallery viewer with edit', viewer);

    // Edit: the editor opens on the original; Save keeps the original and adds the edit.
    await viewer.getByTestId('private-gallery-edit').click();
    let editor = page.getByTestId('photo-editor');
    await expect(editor.getByTestId('photo-editor-tools')).toBeVisible();
    await expect(editor.getByTestId('photo-editor-done')).toContainText('Save');
    await expect(editor.getByTestId('photo-editor-as-is')).toHaveText('Use the original');
    await editor.getByRole('button', { name: 'Noir filter' }).click();
    await editor.getByTestId('photo-editor-done').click();
    await expect(editor).toHaveCount(0);
    await expect(viewer.getByTestId('private-gallery-edited')).toContainText('Edited by you. The original is kept.');
    await expect(tile).toHaveAttribute('data-edited', 'true');
    await check(page, info, run, 'gallery viewer edited', viewer);

    let list = await edits(key, server);
    expect(list).toHaveLength(1);
    const first = list[0].payload;
    expect(first).toMatchObject({ t: 'gallery_edit', of: ORIGINAL, mime: 'image/jpeg', width: 200, height: 150 });
    expect(first.recipe).toMatchObject({ filter: 'noir' });
    // The edit is ciphertext on the server and opens to the edited picture; the original is exactly as it was.
    const edited = server.blobs[first.blob!];
    expect(edited[0] === 0xff && edited[1] === 0xd8).toBe(false);
    const editedPicture = await readPicture(page, await decryptBytes(key, new Uint8Array(edited)), 'image/jpeg', [0.25, 0.5]);
    expect(grey(editedPicture.rgb)).toBe(true);
    expect(server.blobs[ORIGINAL].equals(originalSealed)).toBe(true);
    // Every gallery record is one size to the server, an edit included, and names nothing in the clear.
    expect(new Set(server.entries.map((e) => e.sealed.length)).size).toBe(1);
    for (const e of server.entries) expect(e.sealed).not.toContain(ORIGINAL);
    // The viewer shows the edit: the picture on screen is grey where the original is red.
    const onScreen = await viewer.getByTestId('private-gallery-photo').evaluate(async (img: HTMLImageElement) => {
      const c = document.createElement('canvas');
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const ctx = c.getContext('2d')!;
      ctx.drawImage(img, 0, 0);
      const d = ctx.getImageData(Math.floor(c.width * 0.25), Math.floor(c.height / 2), 1, 1).data;
      return [d[0], d[1], d[2]];
    });
    expect(grey(onScreen), `shown: ${onScreen}`).toBe(true);

    // Edit again: from the original, with the recipe it was made with (Noir is on), now Ocean.
    await viewer.getByTestId('private-gallery-edit-again').click();
    editor = page.getByTestId('photo-editor');
    await expect(editor.getByRole('button', { name: 'Noir filter' })).toHaveAttribute('aria-pressed', 'true');
    await editor.getByRole('button', { name: 'Ocean filter' }).click();
    await editor.getByTestId('photo-editor-done').click();
    await expect(editor).toHaveCount(0);
    await expect.poll(async () => (await edits(key, server)).map((e) => e.payload.recipe?.filter)).toEqual(['ocean']);
    list = await edits(key, server);
    expect(list[0].payload.of).toBe(ORIGINAL);
    // The first edit (my own) was tidied away: its record and its blob; the original never.
    expect(server.blobsDeleted).toContain(first.blob);
    expect(server.blobsDeleted).not.toContain(ORIGINAL);

    // Revert: the original is shown again.
    const second = list[0].payload;
    await viewer.getByTestId('private-gallery-revert').click();
    await expect(viewer.getByTestId('private-gallery-edited')).toHaveCount(0);
    await expect(tile).not.toHaveAttribute('data-edited', 'true');
    await expect.poll(async () => (await edits(key, server)).map((e) => Boolean(e.payload.revert))).toEqual([true]);
    expect(server.blobsDeleted).toContain(second.blob);
    expect(server.blobs[ORIGINAL].equals(originalSealed)).toBe(true);

    // Edited once more, then the picture is deleted: its edits go with it.
    await viewer.getByTestId('private-gallery-edit').click();
    editor = page.getByTestId('photo-editor');
    await editor.getByRole('button', { name: 'Pop filter' }).click();
    await editor.getByTestId('photo-editor-done').click();
    await expect(viewer.getByTestId('private-gallery-edited')).toBeVisible();
    const third = (await edits(key, server))[0].payload;
    await viewer.getByTestId('private-gallery-delete').click();
    await expect.poll(() => server.blobsDeleted.includes(ORIGINAL)).toBe(true);
    await expect.poll(() => server.blobsDeleted.includes(third.blob!)).toBe(true);
    await expect.poll(async () => (await edits(key, server)).length).toBe(0);
    done(run);
  });

  test('a family chat: the photo picker opens the editor and only the edited picture is sent', async ({ page }, info) => {
    const { server } = await chat();
    const run = await open(page, server);
    const png = await drawnPng(page);
    await page.getByRole('list', { name: 'Conversations' }).getByText('Camping trip planning').first().click();
    await page.getByRole('button', { name: 'More options' }).click();
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('menuitem', { name: 'Photo or video' }).click();
    await (await chooser).setFiles({ name: 'campfire.png', mimeType: 'image/png', buffer: png });
    let editor = page.getByTestId('photo-editor');
    await expect(editor.getByTestId('photo-editor-tools')).toBeVisible();
    await expect(editor.getByTestId('photo-editor-done')).toContainText('Done');
    await expect(editor.getByTestId('photo-editor-as-is')).toHaveText('Use as is');
    await check(page, info, run, 'editor from the family picker', editor);
    // Family-friendly names only: the stickers and filters children see.
    await editor.getByTestId('photo-editor-tool-stickers').click();
    for (const name of ['heart', 'star', 'rainbow', 'balloon']) await expect(editor.getByRole('button', { name: `Add a ${name} sticker` })).toBeVisible();
    await editor.getByTestId('photo-editor-tool-filters').click();
    await editor.getByRole('button', { name: 'Noir filter' }).click();
    await editor.getByTestId('photo-editor-done').click();
    await expect(editor).toHaveCount(0);

    // In the tray as the edited picture, with Edit to change it again before sending.
    const tray = page.getByTestId('attachment-tray');
    await expect(tray.getByRole('img', { name: 'campfire-edited.jpg' })).toBeVisible();
    await expect(tray.getByTestId('staged-edit')).toBeVisible();
    await check(page, info, run, 'family tray with edit', page.getByTestId('attachment-tray'));
    await tray.getByTestId('staged-edit').click();
    editor = page.getByTestId('photo-editor');
    await expect(editor.getByTestId('photo-editor-tools')).toBeVisible();
    await editor.getByTestId('photo-editor-close').click();
    await expect(editor).toHaveCount(0);
    await expect(tray.getByRole('img', { name: 'campfire-edited.jpg' })).toBeVisible();

    await page.getByRole('button', { name: 'Send message' }).click();
    await expect.poll(() => server.talkFiles.length).toBe(1);
    const sent = server.talkFiles[0];
    expect(sent).toMatchObject({ token: 'room-trip', file_name: 'campfire-edited.jpg', mime_type: 'image/jpeg' });
    // Only the picture: no recipe, no original.
    for (const field of Object.keys(sent)) expect(['token', 'file_base64', 'file_name', 'mime_type', 'caption', 'as_user']).toContain(field);
    expect(JSON.stringify(sent)).not.toContain('recipe');
    const picture = await readPicture(page, Buffer.from(String(sent.file_base64), 'base64'), 'image/jpeg', [0.25, 0.5]);
    expect(grey(picture.rgb)).toBe(true);
    done(run);
  });

  test('clear of the Android bars, with 3-button nav and large text', async ({ page }, info) => {
    const p2 = await page.context().newPage();
    await p2.goto('about:blank');
    const png = await drawnPng(p2);
    await p2.close();
    const { server } = await chat(png);
    const run = await open(page, server);
    const STATUS_BAR = 32;
    const NAV_BAR = 48;
    await page.evaluate(([top, bottom]) => {
      document.documentElement.style.setProperty('--safe-area-inset-top', `${top}px`);
      document.documentElement.style.setProperty('--safe-area-inset-bottom', `${bottom}px`);
      document.documentElement.style.fontSize = '130%';
    }, [STATUS_BAR, NAV_BAR]);
    const viewport = page.viewportSize()!;
    const clear = async (scope: Locator, where: string) => {
      await settle(page);
      const boxes = await scope.locator('button:visible, input:visible, textarea:visible').evaluateAll((els) =>
        els.map((el) => {
          el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' as ScrollBehavior });
          const b = el.getBoundingClientRect();
          return { name: el.getAttribute('aria-label') || el.textContent?.trim() || el.tagName, top: b.top, bottom: b.bottom };
        }),
      );
      for (const b of boxes) {
        expect(b.top, `${where}: ${b.name} is under the status bar`).toBeGreaterThanOrEqual(STATUS_BAR - 0.5);
        expect(b.bottom, `${where}: ${b.name} is under the nav bar`).toBeLessThanOrEqual(viewport.height - NAV_BAR + 0.5);
      }
      await check(page, info, run, `bars ${where}`, scope);
    };

    // The gallery viewer, edited: Edit, Edit again and Revert all within reach.
    await unlock(page);
    await page.getByTestId('private-gallery-open').click();
    await page.getByTestId('private-gallery-tile').first().click();
    const viewer = page.getByTestId('private-gallery-viewer');
    await viewer.getByTestId('private-gallery-edit').click();
    const editor = page.getByTestId('photo-editor');
    await editor.getByRole('button', { name: 'Warm filter' }).click();
    await editor.getByTestId('photo-editor-done').click();
    await expect(viewer.getByTestId('private-gallery-edited')).toBeVisible();
    await clear(viewer, 'gallery viewer edited');
    await viewer.getByRole('button', { name: 'Back to the gallery' }).click();
    await page.getByRole('button', { name: 'Close gallery' }).click();

    // The private chat picker's editor.
    const chooser = page.waitForEvent('filechooser');
    await page.getByTestId('private-pick').click();
    await (await chooser).setFiles({ name: 'garden.png', mimeType: 'image/png', buffer: png });
    await expect(editor.getByTestId('photo-editor-tools')).toBeVisible();
    await clear(editor, 'private picker editor');
    await editor.getByTestId('photo-editor-close').click();
    done(run);
  });

  test('clear of the Android bars in a family chat tray, with 3-button nav and large text', async ({ page }, info) => {
    const { server } = await chat();
    const run = await open(page, server);
    const png = await drawnPng(page);
    const STATUS_BAR = 32;
    const NAV_BAR = 48;
    await page.evaluate(([top, bottom]) => {
      document.documentElement.style.setProperty('--safe-area-inset-top', `${top}px`);
      document.documentElement.style.setProperty('--safe-area-inset-bottom', `${bottom}px`);
      document.documentElement.style.fontSize = '130%';
    }, [STATUS_BAR, NAV_BAR]);
    await page.getByRole('list', { name: 'Conversations' }).getByText('Camping trip planning').first().click();
    await page.getByRole('button', { name: 'More options' }).click();
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('menuitem', { name: 'Photo or video' }).click();
    await (await chooser).setFiles([
      { name: 'one.png', mimeType: 'image/png', buffer: png },
      { name: 'two.png', mimeType: 'image/png', buffer: png },
    ]);
    // Several at once go straight to the tray, each with Edit.
    const tray = page.getByTestId('attachment-tray');
    await expect(tray.getByTestId('staged-edit')).toHaveCount(2);
    await expect(page.getByTestId('photo-editor')).toHaveCount(0);
    const viewport = page.viewportSize()!;
    await settle(page);
    for (const box of await tray.getByTestId('staged-edit').evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON() as DOMRect))) {
      expect(box.top).toBeGreaterThanOrEqual(STATUS_BAR - 0.5);
      expect(box.bottom).toBeLessThanOrEqual(viewport.height - NAV_BAR + 0.5);
    }
    await check(page, info, run, 'bars family tray', tray);
    // Edit the second: it alone is replaced.
    await tray.getByTestId('staged-edit').last().click();
    const editor = page.getByTestId('photo-editor');
    await editor.getByRole('button', { name: 'Mono filter' }).click();
    await editor.getByTestId('photo-editor-done').click();
    await expect(tray.getByRole('img', { name: 'two-edited.jpg' })).toBeVisible();
    await expect(tray.getByRole('img', { name: 'one.png' })).toBeVisible();
    done(run);
  });
});
