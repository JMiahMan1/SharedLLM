import { test, expect as baseExpect, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, CONVERSATIONS } from '../fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload } from '../fixtures/pageAudit';
import { decryptBytes, deriveKey, makeVerifier, openMessage, randomBytes, sealMessage, toBase64, PBKDF2_ITERATIONS } from '../../src/lib/privateChatCrypto';
import { fakeGif } from '../../src/test/fakes/fakeGif';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * The on-device photo editor, opened from the private chat's camera, on every
 * form factor: every tool's screen gets the shared audit (44px targets on
 * phones, nothing clipped or off the screen, clear of the system bars), a
 * filter, crop, words, a sticker and a drawn line are applied and sent, and
 * what reaches the server is ciphertext that decrypts to the edited picture.
 * "Send as is" sends the camera's own picture. Then the animated path runs in
 * the real browser: every frame of a GIF read with ImageDecoder, edited, and
 * written back as a GIF with the same frame count and timing.
 * Hermetic: the server is mocked, the camera is a drawn stream.
 */

const PASSWORD = 'a long shared sentence';
const TOKEN = 'roomeditor1';
const SEC = Math.floor(Date.now() / 1000);

interface Server {
  chats: Array<Record<string, unknown>>;
  messages: Array<Record<string, unknown>>;
  uploads: Buffer[];
  posted: string[];
}

interface Run {
  mocks: HomeMocks;
  errors: string[];
  found: string[];
  /** While the editor was open: anything sent (not a GET), or any request off this origin. */
  outside: string[];
  editing: boolean;
}

async function serve(page: Page, server: Server): Promise<void> {
  await page.route(/\/api\/(private-chat\/|communication\/talk\/(conversations|messages|read)(\?|$|\/))/, async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/api/private-chat/status') {
      return route.fulfill({ json: { eligible: true, reason: null, chats: server.chats, min_iterations: PBKDF2_ITERATIONS, partners: [{ username: 'robin', display_name: 'Robin' }] } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}` && req.method() === 'GET') return route.fulfill({ json: server.chats[0] });
    if (p === `/api/private-chat/chats/${TOKEN}/blobs` && req.method() === 'POST') {
      const body = req.postDataBuffer() ?? Buffer.alloc(0);
      server.uploads.push(body);
      return route.fulfill({ json: { id: server.uploads.length.toString(16).padStart(32, '0'), size: body.length } });
    }
    if (/\/blobs\/[0-9a-f]+$/.test(p)) {
      const i = parseInt(p.split('/').pop()!, 16) - 1;
      return server.uploads[i] ? route.fulfill({ body: server.uploads[i], contentType: 'application/octet-stream' }) : route.fulfill({ status: 404, json: {} });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/gallery`) return route.fulfill({ json: { entries: [] } });
    if (p === `/api/private-chat/chats/${TOKEN}/marks`) return route.fulfill({ json: { marks: [] } });
    if (p === `/api/private-chat/chats/${TOKEN}/reminders`) {
      return route.fulfill({ json: { enabled: true, available: true, delay_s: 21600, question_delay_s: 7200, quiet: '21:30-07:30', daily_max: 2 } });
    }
    if (p === '/api/communication/talk/conversations') {
      const own = [{ token: TOKEN, display_name: 'Private chat', type: 2, private: true, last_message: 'Encrypted message', last_message_actor: 'Parent', last_message_actor_id: 'parent',
        last_message_timestamp: SEC - 30, last_activity: SEC - 30, unread_messages: 0, last_common_read: 1, last_read: 1, can_moderate: true, storage: 'store' }];
      return route.fulfill({ json: { status: 'SUCCESS', detail: { conversations: [...own, ...CONVERSATIONS] } } });
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
      const message = { id: server.messages.length + 1, token: TOKEN, actor_type: 'users', actor_id: 'parent', actor_display_name: 'Parent', timestamp: Math.floor(Date.now() / 1000),
        message_type: 'comment', message: posted, deleted: false };
      server.messages.push(message);
      return route.fulfill({ json: { status: 'SUCCESS', message: 'Sent.', source: 'store', detail: { message } } });
    }
    if (p === `/api/private-chat/chats/${TOKEN}/read`) {
      return route.fulfill({ json: { status: 'SUCCESS', source: 'store', detail: { token: TOKEN, last_read: server.messages.length, unread_messages: 0 } } });
    }
    if (/^\/api\/communication\/talk\/messages/.test(p)) return route.fulfill({ status: 500, json: { detail: 'The private chat must not go through Talk' } });
    return route.fallback();
  });
}

async function chat(): Promise<{ server: Server; key: CryptoKey }> {
  const kdf = { salt: toBase64(randomBytes(16)), iterations: PBKDF2_ITERATIONS };
  const key = await deriveKey(PASSWORD, kdf);
  const server: Server = {
    chats: [{ token: TOKEN, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin', created_at: SEC - 86400, version: 1,
      kdf: { name: 'PBKDF2-SHA256', ...kdf }, verifier: await makeVerifier(key) }],
    messages: [{ id: 1, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', timestamp: SEC - 600, message_type: 'comment',
      message: await sealMessage(key, { t: 'text', text: 'Show me the view' }) }],
    uploads: [],
    posted: [],
  };
  return { server, key };
}

/** A stand-in camera: a drawn stream, coloured on the left and white on the right, so a filter and a crop can be read back. */
async function fakeCamera(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          const canvas = document.createElement('canvas');
          canvas.width = 640;
          canvas.height = 480;
          const ctx = canvas.getContext('2d')!;
          const draw = () => {
            ctx.fillStyle = '#d92626';
            ctx.fillRect(0, 0, 320, 480);
            ctx.fillStyle = '#2563eb';
            ctx.fillRect(320, 0, 320, 480);
          };
          draw();
          const timer = setInterval(draw, 100);
          const stream = canvas.captureStream(10);
          for (const track of stream.getTracks()) {
            const stop = track.stop.bind(track);
            track.stop = () => {
              clearInterval(timer);
              stop();
            };
          }
          return stream;
        },
      },
    });
  });
}

async function open(page: Page, server: Server): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await serve(page, server);
  const run: Run = { mocks, errors: [], found: [], outside: [], editing: false };
  page.on('pageerror', (e) => run.errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) run.errors.push(m.text().slice(0, 400));
  });
  // Editing never sends anything anywhere: while the editor is open the page may only load its own code (GET, same
  // origin) and keep polling. The app shell's web font stylesheet is the one outside request it already makes.
  page.on('request', (req) => {
    if (!run.editing) return;
    const url = new URL(req.url());
    if (!['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol)) return;
    if (req.method() !== 'GET') run.outside.push(`${req.method()} ${req.url()}`);
    else if (!/^(localhost|127\.0\.0\.1)$/.test(url.hostname) && !/^fonts\.(googleapis|gstatic)\.com$/.test(url.hostname)) run.outside.push(req.url());
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
  expect(run.outside, `requests while editing: ${run.outside.join(', ')}`).toEqual([]);
}

async function takePicture(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: /Private chat/ }).first().click();
  await page.getByTestId('private-password').fill(PASSWORD);
  await page.getByTestId('private-unlock').click();
  await expect(page.getByTestId('private-feed').getByText('Show me the view')).toBeVisible();
  await page.getByTestId('private-camera-open').click();
  const camera = page.getByTestId('private-camera');
  await expect(camera.getByTestId('private-camera-shutter')).toBeEnabled();
  await camera.getByTestId('private-camera-shutter').click();
  await expect(camera.getByTestId('private-camera-still')).toBeVisible();
  return camera;
}

/** Decode a picture's bytes in the page and read its size and the colour at a point (fractions). */
async function readPicture(page: Page, bytes: Uint8Array, mime: string, at: [number, number]): Promise<{ width: number; height: number; rgb: number[] }> {
  return page.evaluate(
    async ({ b64, mime, at }) => {
      const bin = atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) arr[i] = bin.charCodeAt(i);
      const bitmap = await createImageBitmap(new Blob([arr], { type: mime }));
      const c = document.createElement('canvas');
      c.width = bitmap.width;
      c.height = bitmap.height;
      const ctx = c.getContext('2d')!;
      ctx.drawImage(bitmap, 0, 0);
      const d = ctx.getImageData(Math.floor(at[0] * c.width), Math.floor(at[1] * c.height), 1, 1).data;
      return { width: c.width, height: c.height, rgb: [d[0], d[1], d[2]] };
    },
    { b64: Buffer.from(bytes).toString('base64'), mime, at },
  );
}

test.describe('the photo editor on every form factor', () => {
  test.describe.configure({ timeout: 150_000 });

  test('edit a camera picture: every tool, then send it encrypted', async ({ page }, info) => {
    await fakeCamera(page);
    const { server, key } = await chat();
    const run = await open(page, server);
    const camera = await takePicture(page);
    await check(page, info, run, 'camera with edit', camera);

    run.editing = true;
    await camera.getByTestId('private-camera-edit').click();
    const editor = page.getByTestId('photo-editor');
    await expect(editor.getByTestId('photo-editor-tools')).toBeVisible();
    await expect(editor.getByTestId('photo-editor-preview')).toBeVisible();
    await check(page, info, run, 'editor filters', editor);

    // Filters: one tap, shown at once.
    await editor.getByRole('button', { name: 'Noir filter' }).click();
    await expect(editor.getByRole('button', { name: 'Noir filter' })).toHaveAttribute('aria-pressed', 'true');

    await editor.getByTestId('photo-editor-tool-adjust').click();
    await check(page, info, run, 'editor adjust', editor);
    await editor.getByLabel('Brightness').fill('20');

    // Crop to a square: the crop box is on the picture, its corners within reach.
    await editor.getByTestId('photo-editor-tool-crop').click();
    await editor.getByTestId('photo-editor-aspect-square').click();
    await expect(editor.getByTestId('photo-editor-crop-box')).toBeVisible();
    await check(page, info, run, 'editor crop', editor);
    // Slide the square to the left edge with the keyboard (the coloured half).
    const move = editor.getByRole('button', { name: /Move the crop/ });
    await move.focus();
    for (let i = 0; i < 20; i += 1) await page.keyboard.press('ArrowLeft');

    // Words with a background.
    await editor.getByTestId('photo-editor-tool-text').click();
    await editor.getByTestId('photo-editor-text-input').fill('Hello');
    await editor.getByTestId('photo-editor-text-add').click();
    await expect(editor.getByTestId('photo-editor-overlay')).toHaveCount(1);
    await check(page, info, run, 'editor text', editor);

    // A sticker, dragged into the bin and gone; then one that stays.
    await editor.getByTestId('photo-editor-tool-stickers').click();
    await check(page, info, run, 'editor stickers', editor);
    await editor.getByTestId('photo-editor-sticker-star').click();
    await expect(editor.getByTestId('photo-editor-overlay')).toHaveCount(2);
    const star = editor.getByTestId('photo-editor-overlay').last();
    const from = (await star.boundingBox())!;
    const stage = (await editor.getByTestId('photo-editor-stage').boundingBox())!;
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2 + 30, { steps: 4 });
    const bin = editor.getByTestId('photo-editor-bin');
    await expect(bin).toBeVisible();
    const binBox = (await bin.boundingBox())!;
    await page.mouse.move(binBox.x + binBox.width / 2, binBox.y + binBox.height / 2, { steps: 8 });
    await page.mouse.up();
    await expect(editor.getByTestId('photo-editor-overlay')).toHaveCount(1);
    expect(stage.height).toBeGreaterThan(100);
    await editor.getByTestId('photo-editor-sticker-heart').click();
    await expect(editor.getByTestId('photo-editor-overlay')).toHaveCount(2);

    // A drawn line, undone, drawn again.
    await editor.getByTestId('photo-editor-tool-draw').click();
    await check(page, info, run, 'editor draw', editor);
    const pic = (await editor.getByTestId('photo-editor-picture').boundingBox())!;
    const line = async () => {
      await page.mouse.move(pic.x + pic.width * 0.2, pic.y + pic.height * 0.8);
      await page.mouse.down();
      await page.mouse.move(pic.x + pic.width * 0.5, pic.y + pic.height * 0.85, { steps: 6 });
      await page.mouse.up();
    };
    await line();
    await expect(editor.getByTestId('photo-editor-undo')).toBeEnabled();
    await editor.getByTestId('photo-editor-undo').click();
    await expect(editor.getByTestId('photo-editor-undo')).toBeDisabled();
    await line();

    run.editing = false;
    await editor.getByTestId('photo-editor-done').click();
    await expect(editor).toHaveCount(0);
    await expect(camera).toHaveCount(0);
    await expect.poll(() => server.posted.length).toBe(1);

    // On the wire: ciphertext only. Opened with the key: a square JPEG, grey (Noir) where the red half was.
    const [upload] = server.uploads;
    expect(upload[0] === 0xff && upload[1] === 0xd8).toBe(false);
    const sent = await openMessage(key, server.posted[0]);
    expect(sent).toMatchObject({ t: 'image', mime: 'image/jpeg' });
    const plain = await decryptBytes(key, new Uint8Array(upload));
    expect(plain[0] === 0xff && plain[1] === 0xd8).toBe(true);
    const picture = await readPicture(page, plain, 'image/jpeg', [0.25, 0.3]);
    expect(picture.width).toBe(picture.height);
    expect(picture.width).toBeGreaterThanOrEqual(470);
    const [r, g, b] = picture.rgb;
    expect(Math.max(r, g, b) - Math.min(r, g, b)).toBeLessThan(12);
    done(run);
  });

  test('send as is: the camera picture, untouched by the editor', async ({ page }) => {
    await fakeCamera(page);
    const { server, key } = await chat();
    const run = await open(page, server);
    const camera = await takePicture(page);
    run.editing = true;
    await camera.getByTestId('private-camera-edit').click();
    const editor = page.getByTestId('photo-editor');
    await expect(editor.getByTestId('photo-editor-tools')).toBeVisible();
    await editor.getByRole('button', { name: 'Ocean filter' }).click();
    // Closing the editor comes back to the taken picture, still there.
    await editor.getByTestId('photo-editor-close').click();
    await expect(editor).toHaveCount(0);
    await expect(camera.getByTestId('private-camera-still')).toBeVisible();
    await camera.getByTestId('private-camera-edit').click();
    run.editing = false;
    await page.getByTestId('photo-editor-as-is').click();
    await expect(camera).toHaveCount(0);
    await expect.poll(() => server.posted.length).toBe(1);
    const plain = await decryptBytes(key, new Uint8Array(server.uploads[0]));
    const picture = await readPicture(page, plain, 'image/jpeg', [0.25, 0.5]);
    expect([picture.width, picture.height]).toEqual([640, 480]);
    // The camera's own red: no filter on it.
    expect(picture.rgb[0]).toBeGreaterThan(180);
    expect(picture.rgb[2]).toBeLessThan(80);
    done(run);
  });

  test('clear of the Android bars, with 3-button nav and large text', async ({ page }, info) => {
    await fakeCamera(page);
    const { server } = await chat();
    const run = await open(page, server);
    // The Android app's edge-to-edge insets as Capacitor injects them (env() reads 0 there): a status bar and the
    // taller 3-button navigation bar. Large text: the WebView scales the root font size, as Android's font size does.
    const STATUS_BAR = 32;
    const NAV_BAR = 48;
    await page.evaluate(([top, bottom]) => {
      document.documentElement.style.setProperty('--safe-area-inset-top', `${top}px`);
      document.documentElement.style.setProperty('--safe-area-inset-bottom', `${bottom}px`);
      document.documentElement.style.fontSize = '130%';
    }, [STATUS_BAR, NAV_BAR]);
    const camera = await takePicture(page);
    await camera.getByTestId('private-camera-edit').click();
    const editor = page.getByTestId('photo-editor');
    await expect(editor.getByTestId('photo-editor-tools')).toBeVisible();
    const viewport = page.viewportSize()!;
    for (const tool of ['filters', 'adjust', 'crop', 'text', 'stickers', 'draw']) {
      await editor.getByTestId(`photo-editor-tool-${tool}`).click();
      if (tool === 'stickers') await editor.getByTestId('photo-editor-sticker-heart').click();
      await settle(page);
      const boxes = await editor.locator('button:visible, input:visible, textarea:visible').evaluateAll((els) =>
        els.map((el) => {
          // Scrolled to, as a thumb would: a control further down a scrolling panel must still come clear of the bars.
          el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' as ScrollBehavior });
          const b = el.getBoundingClientRect();
          return { name: el.getAttribute('aria-label') || el.textContent?.trim() || el.tagName, top: b.top, bottom: b.bottom };
        }),
      );
      for (const b of boxes) {
        expect(b.top, `${tool}: ${b.name} is under the status bar`).toBeGreaterThanOrEqual(STATUS_BAR - 0.5);
        expect(b.bottom, `${tool}: ${b.name} is under the nav bar`).toBeLessThanOrEqual(viewport.height - NAV_BAR + 0.5);
      }
      await check(page, info, run, `editor bars ${tool}`, editor);
    }
    done(run);
  });

  test('an animated GIF: every frame edited and written back with its timing', async ({ page }) => {
    test.skip(process.env.UI_SERVE === 'preview', 'imports the editor module from the dev server');
    test.skip(test.info().project.use.browserName === 'webkit', 'Safari has no ImageDecoder: the editor edits the first frame there, which the unit tests cover');
    await quietHotReload(page);
    await page.goto('/favicon.ico').catch(() => {});
    const gif = fakeGif(40, 30, [[220, 38, 38], [37, 99, 235], [22, 163, 74]], 15);
    const out = await page.evaluate(async (b64) => {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
      const source = new Blob([bytes], { type: 'image/gif' });
      const m = await import('/src/lib/photoEdit/index.ts' as string);
      const decoded = await m.decodePicture(source);
      const recipe = { ...m.emptyRecipe(), filter: 'mono', rotate: 90, overlays: [{ id: 't', kind: 'text', text: 'Hi', font: 'sans', color: '#ffffff', pill: true, x: 0.5, y: 0.5, scale: 1, rotation: 0 }] };
      const result = await m.exportEdit(decoded, recipe, source, { maxBytes: 25 * 1024 * 1024 });
      // Read the result back with the browser's own decoder.
      const decoder = new ImageDecoder({ data: await result.blob.arrayBuffer(), type: 'image/gif' });
      await decoder.tracks.ready;
      await decoder.completed;
      const frames: Array<{ w: number; h: number; ms: number; grey: boolean }> = [];
      for (let i = 0; i < decoder.tracks.selectedTrack!.frameCount; i += 1) {
        const { image } = await decoder.decode({ frameIndex: i });
        const c = new OffscreenCanvas(image.displayWidth, image.displayHeight);
        const ctx = c.getContext('2d')!;
        ctx.drawImage(image, 0, 0);
        const d = ctx.getImageData(2, 2, 1, 1).data;
        frames.push({ w: image.displayWidth, h: image.displayHeight, ms: (image.duration ?? 0) / 1000, grey: Math.max(d[0], d[1], d[2]) - Math.min(d[0], d[1], d[2]) < 10 });
        image.close();
      }
      decoder.close();
      return { animated: decoded.animated, inFrames: decoded.frames.length, mime: result.mime, frames, sameOriginal: result.original === source, recipe: result.recipe };
    }, Buffer.from(gif).toString('base64'));
    expect(out.animated).toBe(true);
    expect(out.inFrames).toBe(3);
    expect(out.mime).toBe('image/gif');
    expect(out.sameOriginal).toBe(true);
    expect(out.recipe).toMatchObject({ filter: 'mono', rotate: 90 });
    expect(out.frames).toHaveLength(3);
    for (const f of out.frames) {
      // Turned a quarter: 30 wide, 40 tall; 150 ms a frame as the source said; grey from the Mono filter.
      expect([f.w, f.h]).toEqual([30, 40]);
      expect(f.ms).toBe(150);
      expect(f.grey).toBe(true);
    }
  });
});
