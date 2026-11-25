import { test, expect as baseExpect, type Locator, type Page, type Route, type TestInfo } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, CONVERSATIONS } from '../fixtures/familyPagesMocks';
import { screenFindings, settle, shot, quietHotReload } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * "Add to home screen" for a chat conversation, on every form factor. The web
 * build never offers it; with a stand-in for the Android plugin it sits in the
 * conversation's details sheet, sends the plugin the chat's id, name, page and
 * picture (never a message), and a private chat's shortcut goes to its lock
 * screen. A shortcut to a chat that is gone lands on the list with a notice.
 * Hermetic: the server is mocked.
 */

const SEC = Math.floor(Date.now() / 1000);
const PRIVATE = 'roomprivate1';
// A 4x4 opaque PNG: Jordan's uploaded photo.
const PHOTO = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAEElEQVR4nGM4EaABRwzEcQASUhQBFGG4BQAAAABJRU5ErkJggg==',
  'base64',
);

interface Run { mocks: HomeMocks; errors: string[]; found: string[] }

declare global {
  interface Window {
    __pinned?: Array<Record<string, unknown>>;
    __disabled?: Array<Record<string, unknown>>;
    __offered?: Array<Record<string, unknown>>;
  }
}

async function nativeStandIn(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__pinned = [];
    window.__disabled = [];
    window.__offered = [];
    (window as unknown as { __jarvisChatShortcutMock: unknown }).__jarvisChatShortcutMock = {
      supported: async () => ({ supported: true }),
      pin: async (request: Record<string, unknown>) => {
        window.__pinned!.push(request);
        return { requested: true, updated: false };
      },
      disable: async (options: Record<string, unknown>) => {
        window.__disabled!.push(options);
      },
      offer: async (request: Record<string, unknown>) => {
        window.__offered!.push(request);
      },
    };
  });
}

async function open(page: Page, withPrivate = false, beloved = false): Promise<Run> {
  await quietHotReload(page);
  const mocks = await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  // Registered after the shared mocks, so these answer first.
  await page.route(/\/api\/(users\/family-directory|users\/jordan\/avatar\/image|private-chat\/status|communication\/talk\/conversations)(\?|$)/, async (route: Route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/api/users/family-directory') {
      return route.fulfill({ json: [
        { username: 'parent', display_name: 'Parent', nextcloud_user: 'parent', avatar: null },
        { username: 'robin', display_name: 'Robin', nextcloud_user: 'robin', avatar: 'preset:balaams-donkey' },
        { username: 'jordan', display_name: 'Jordan', nextcloud_user: 'jordan', avatar: 'upload:3' },
      ] });
    }
    if (p === '/api/users/jordan/avatar/image') return route.fulfill({ body: PHOTO, contentType: 'image/png' });
    if (p === '/api/private-chat/status') {
      return route.fulfill({ json: { eligible: true, reason: null, min_iterations: 600000, partners: [{ username: 'robin', display_name: 'Robin' }],
        chats: withPrivate ? [{ token: PRIVATE, members: ['parent', 'robin'], partner: 'robin', partner_name: 'Robin', created_by: 'robin',
          created_at: SEC - 86400, version: 2, kdf: { name: 'PBKDF2-SHA256', salt: 'c2FsdHNhbHRzYWx0c2FsdA==', iterations: 600000 },
          wrapped_key: { iv: 'aXZpdml2aXZpdml2', ct: 'Y3Q=' }, verifier: { iv: 'aXZpdml2aXZpdml2', ct: 'Y3Q=' }, beloved }] : [] } });
    }
    const own = withPrivate
      ? [{ token: PRIVATE, display_name: 'Private chat', type: 2, private: true, last_message: 'Encrypted message', last_activity: SEC - 30,
        unread_messages: 0, can_moderate: true }]
      : [];
    return route.fulfill({ json: { status: 'SUCCESS', detail: { conversations: [...own, ...CONVERSATIONS] } } });
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && /React UI error|Uncaught|is not a function|Cannot read prop/.test(m.text())) errors.push(m.text().slice(0, 400));
  });
  return { mocks, errors, found: [] };
}

async function details(page: Page, conversation: RegExp) {
  await page.goto('/family');
  await page.getByRole('list', { name: 'Conversations' }).getByRole('button', { name: conversation }).first().click();
  await page.getByRole('button', { name: 'Conversation details' }).click();
  const sheet = page.getByTestId('conversation-info');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByTestId('chat-default-toggle')).toBeVisible();
  return sheet;
}

async function check(page: Page, info: TestInfo, run: Run, where: string): Promise<void> {
  await settle(page);
  run.found.push(...await screenFindings(page, page.getByRole('dialog').last(), where));
  await shot(page, info, where.replace(/\W+/g, '-'));
}

function done(run: Run) {
  expect(run.found, run.found.join('\n')).toEqual([]);
  expect(run.errors, `page errors: ${run.errors.join(' | ')}`).toEqual([]);
  expect(run.mocks.unmocked, `unmocked: ${run.mocks.unmocked.join(', ')}`).toEqual([]);
}

/**
 * The CI job that checks layout against the production build (UI_SERVE=preview). src/plugins/chatShortcut.ts
 * reads the stand-in only in a dev build, so there a stand-in a page script plants must change nothing: the
 * sheet offers no shortcut and the plugin is never called. The dev-server jobs run the app flow itself.
 */
const PRODUCTION_BUILD = process.env.UI_SERVE === 'preview';

async function standInIgnored(page: Page, sheet: Locator, run: Run): Promise<void> {
  await expect(sheet.getByTestId('chat-delete')).toBeVisible();
  await expect(sheet.getByTestId('chat-home-shortcut')).toHaveCount(0);
  await expect(sheet.getByTestId('chat-beloved-shortcut')).toHaveCount(0);
  expect(await page.evaluate(() => [window.__pinned, window.__offered, window.__disabled])).toEqual([[], [], []]);
  done(run);
}

test.describe('chat home-screen shortcuts on every form factor', () => {
  test.describe.configure({ timeout: 90_000 });

  test('the web build never offers it, and the details sheet is unchanged', async ({ page }, info) => {
    const run = await open(page);
    const sheet = await details(page, /Jordan/);
    await expect(sheet.getByTestId('chat-delete')).toBeVisible();
    await expect(sheet.getByTestId('chat-home-shortcut')).toHaveCount(0);
    await check(page, info, run, 'chat details on the web');
    done(run);
  });

  test("in the app: a one-to-one chat is added with the person's name, page and photo", async ({ page }, info) => {
    await nativeStandIn(page);
    const run = await open(page);
    const sheet = await details(page, /Jordan/);
    if (PRODUCTION_BUILD) return standInIgnored(page, sheet, run);
    const add = sheet.getByTestId('chat-home-shortcut');
    await expect(add).toBeVisible();
    await expect(add).toContainText('Add to home screen');
    await check(page, info, run, 'chat details in the app');
    await add.click();
    await expect.poll(() => page.evaluate(() => window.__pinned?.length ?? 0)).toBe(1);
    const pinned = (await page.evaluate(() => window.__pinned![0])) as Record<string, string>;
    expect(pinned).toMatchObject({ id: 'chat:room-jordan', label: 'Jordan', path: '/family?chat=room-jordan', monogram: 'JO' });
    expect(Object.keys(pinned).sort()).toEqual(['color', 'icon', 'id', 'label', 'monogram', 'path']);
    // The photo, drawn to a PNG on the phone.
    expect(Buffer.from(pinned.icon, 'base64').subarray(1, 4).toString()).toBe('PNG');
    await expect(page.getByText('Added to home screen')).toBeVisible();
    done(run);
  });

  test("in the app: the private chat's shortcut goes to its lock screen, never its messages", async ({ page }, info) => {
    await nativeStandIn(page);
    const run = await open(page, true);
    const sheet = await details(page, /Private chat/);
    if (PRODUCTION_BUILD) return standInIgnored(page, sheet, run);
    const add = sheet.getByTestId('chat-home-shortcut');
    await expect(add).toContainText('lock screen');
    await check(page, info, run, 'private chat details in the app');
    await add.click();
    await expect.poll(() => page.evaluate(() => window.__pinned?.length ?? 0)).toBe(1);
    const pinned = (await page.evaluate(() => window.__pinned![0])) as Record<string, string>;
    expect(pinned).toMatchObject({ id: `chat:${PRIVATE}`, label: 'Robin', path: `/family?chat=${PRIVATE}` });
    // Robin's built-in picture, drawn to a PNG.
    expect(Buffer.from(pinned.icon, 'base64').subarray(1, 4).toString()).toBe('PNG');
    expect(JSON.stringify(pinned)).not.toContain('Encrypted message');

    // Opening it also kept it for the launcher's widget list; no Beloved for a chat that is not the couple's.
    const offered = (await page.evaluate(() => window.__offered ?? [])) as Array<Record<string, string>>;
    expect(offered.map((o) => o.id)).toEqual([`chat:${PRIVATE}`]);
    await expect(sheet.getByTestId('chat-beloved-shortcut')).toHaveCount(0);

    // Opening that page is the lock screen.
    await page.goto(pinned.path);
    await expect(page.getByTestId('private-lock')).toBeVisible();
    await expect(page.getByTestId('private-feed')).toHaveCount(0);
    done(run);
  });

  test("in the app: Beloved gets its own icon with the spouse's picture, opening Beloved after the lock screen", async ({ page }, info) => {
    await nativeStandIn(page);
    const run = await open(page, true, true);
    const sheet = await details(page, /Private chat/);
    if (PRODUCTION_BUILD) return standInIgnored(page, sheet, run);
    const add = sheet.getByTestId('chat-beloved-shortcut');
    await expect(add).toContainText('Add Beloved to home screen');
    await check(page, info, run, 'private chat details with Beloved in the app');
    await add.click();
    await expect.poll(() => page.evaluate(() => window.__pinned?.length ?? 0)).toBe(1);
    const pinned = (await page.evaluate(() => window.__pinned![0])) as Record<string, string>;
    expect(pinned).toMatchObject({ id: `chat:${PRIVATE}:beloved`, label: 'Beloved', path: `/family?chat=${PRIVATE}&beloved=1` });
    expect(Buffer.from(pinned.icon, 'base64').subarray(1, 4).toString()).toBe('PNG');
    // Both are kept for the launcher's widget list ("Jarvis chat").
    await expect.poll(async () => ((await page.evaluate(() => window.__offered ?? [])) as Array<Record<string, string>>).map((o) => o.id))
      .toEqual([`chat:${PRIVATE}`, `chat:${PRIVATE}:beloved`]);

    await page.goto(pinned.path);
    await expect(page.getByTestId('private-lock')).toBeVisible();
    await expect(page.getByTestId('private-feed')).toHaveCount(0);
    done(run);
  });

  test('a shortcut to a chat that is gone lands on the list with a notice', async ({ page }) => {
    await nativeStandIn(page);
    const run = await open(page);
    await page.goto('/family?chat=room-gone');
    await expect(page.getByText('This chat is no longer available')).toBeVisible();
    await expect(page.getByRole('list', { name: 'Conversations' })).toBeVisible();
    // The app takes the gone chat's shortcuts off the home screen; a production build never calls the stand-in.
    await expect.poll(() => page.evaluate(() => window.__disabled)).toEqual(PRODUCTION_BUILD ? [] : [
      { id: 'chat:room-gone', message: 'This chat is no longer available' },
      { id: 'chat:room-gone:beloved', message: 'This chat is no longer available' },
    ]);
    done(run);
  });
});
