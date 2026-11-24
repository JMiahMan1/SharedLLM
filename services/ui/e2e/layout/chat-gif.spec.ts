import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, MESSAGES } from '../fixtures/familyPagesMocks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';
import { fakeGif } from '../../src/test/fakes/fakeGif';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * A GIF in a family chat (Nextcloud Talk), on every form factor: it plays,
 * the control pauses it on the frame it was showing, tapping the picture
 * still opens it, and the pause holds when the chat is left and opened again
 * and through a reload, until the session ends. Hermetic: the server is mocked.
 */

const TOKEN = 'room-trip';
const SEC = Math.floor(Date.now() / 1000);
// Two frames, a fifth of a second each: green, then amber.
const GIF = Buffer.from(fakeGif(200, 120, [[22, 163, 74], [245, 158, 11]], 20));
const FILE = { type: 'file', id: '9001', name: 'happy-dance.gif', mimetype: 'image/gif', size: String(GIF.length), path: 'Talk/happy-dance.gif',
  'preview-available': 'yes' };

async function open(page: Page): Promise<{ errors: string[]; files: string[] }> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  const files: string[] = [];
  // Registered after the shared mocks, so these answer first.
  await page.route(/\/api\/communication\/talk\/(messages|file)(\?|$)/, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/file')) {
      files.push(url.search);
      return route.fulfill({ body: GIF, contentType: 'image/gif' });
    }
    const gif = { id: 7, token: TOKEN, actor_type: 'users', actor_id: 'robin', actor_display_name: 'Robin', message: '{file}', timestamp: SEC - 60,
      message_type: 'comment', parameters: { file: FILE } };
    return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [...MESSAGES.map((m) => ({ ...m, token: TOKEN })), gif] } } });
  });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/family');
  return { errors, files };
}

async function openChat(page: Page): Promise<void> {
  await page.getByRole('list', { name: 'Conversations' }).getByText('Camping trip planning').first().click();
  await expect(page.getByTestId('chat-feed')).toContainText('Who is picking up the groceries?');
}

test.describe('a GIF in a family chat', () => {
  test.describe.configure({ timeout: 90_000 });

  test('plays, pauses on its frame, opens as before, and stays paused for the session', async ({ page }, info) => {
    const { errors, files } = await open(page);
    await openChat(page);
    const feed = page.getByTestId('chat-feed');
    const picture = feed.getByTestId('chat-picture');
    await expect(picture).toBeVisible();
    // The file itself (a Nextcloud preview of a GIF is one still frame), once.
    expect(files).toEqual(['?path=Talk%2Fhappy-dance.gif&file_id=9001']);
    const toggle = feed.getByRole('button', { name: 'Pause animation', exact: true });
    await expect(toggle).toHaveAttribute('aria-pressed', 'false');
    await picture.scrollIntoViewIfNeeded();
    await settle(page);
    const found = await screenFindings(page, feed, 'family gif playing');
    await shot(page, info, 'family-gif-playing');

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    const still = feed.getByTestId('animation-still');
    await expect(still).toBeVisible();
    await expect(picture).toBeHidden();
    const pixel = () => still.evaluate((c: HTMLCanvasElement) => Array.from(c.getContext('2d')!.getImageData(100, 60, 1, 1).data.slice(0, 3)));
    const frozen = await pixel();
    await page.waitForTimeout(700);
    expect(await pixel()).toEqual(frozen);
    // No message sheet or reaction bar opened by the press.
    await expect(page.getByTestId('reaction-bar')).toHaveCount(0);
    await settle(page);
    found.push(...await screenFindings(page, feed, 'family gif paused'));
    await shot(page, info, 'family-gif-paused');

    // Tapping the picture still opens it, from the file already loaded, and paused.
    await feed.getByRole('button', { name: 'Open photo happy-dance.gif', exact: true }).click();
    const viewer = page.getByRole('dialog', { name: 'happy-dance.gif' });
    await expect(viewer).toBeVisible();
    await expect(viewer.getByRole('button', { name: 'Pause animation', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(viewer.getByTestId('animation-still')).toBeVisible();
    // Its control does not close the viewer.
    await viewer.getByRole('button', { name: 'Pause animation', exact: true }).click();
    await expect(viewer).toBeVisible();
    await expect(viewer.getByRole('button', { name: 'Pause animation', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await viewer.getByRole('button', { name: 'Pause animation', exact: true }).click();
    await viewer.getByRole('button', { name: 'Close photo' }).click();
    await expect(viewer).toHaveCount(0);
    expect(files).toHaveLength(1);

    // Away to another chat and back: still paused.
    await page.goto('/family');
    await page.getByRole('list', { name: 'Conversations' }).getByText('Jordan').first().click();
    await expect(page.getByTestId('chat-feed')).toBeVisible();
    await page.goto('/family');
    await openChat(page);
    await expect(feed.getByRole('button', { name: 'Pause animation', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(feed.getByTestId('animation-still')).toBeVisible();

    // A reload: still paused. A new session: playing again.
    await page.reload();
    await openChat(page);
    await expect(feed.getByRole('button', { name: 'Pause animation', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.evaluate(() => window.sessionStorage.clear());
    await page.reload();
    await openChat(page);
    await expect(feed.getByRole('button', { name: 'Pause animation', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await expect(feed.getByTestId('animation-still')).toHaveCount(0);

    expect(found, found.join('\n')).toEqual([]);
    expect(errors, errors.join(' | ')).toEqual([]);
  });
});
