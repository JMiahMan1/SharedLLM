import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { setupPlayerMocks } from '../fixtures/mediaPlayback';
import { pushNowPlaying, WEB_PLAYER_ID } from '../fixtures/mediaMocks';

const bookFixture = JSON.parse(fs.readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), '../fixtures/media/media-item-book.json'), 'utf-8',
)) as Record<string, unknown>;

/**
 * A listener opens a book they are part way through and picks up where THEY
 * left off. Music Assistant plays Audiobookshelf books through its own shared
 * ABS account, so on its own it would resume at that account's position (or
 * from the start); the page seeks to the listener's own saved position, which
 * the gateway reads from their ABS account. Leaving the app saves the position
 * at once, so the next device resumes from there.
 */
test('resumes a book from the listener\'s own position and saves it on the way out', async ({ page }) => {
  const { api, ma } = await setupPlayerMocks(page);
  await page.route((url) => url.pathname === '/api/media/item', (route) =>
    route.fulfill({ json: { ...bookFixture, resume_position: 4321, progress: 0.0741, is_finished: false } }));
  const saved: Array<Record<string, unknown>> = [];
  await page.route('**/api/media/abs/progress', async (route) => {
    saved.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ json: { status: 'SUCCESS', item_id: 'book-1', episode_id: null, message: 'ok' } });
  });

  await page.goto('/media/book/book-1');
  await expect(page.getByRole('heading', { name: 'Project Hail Mary', level: 1 })).toBeVisible();
  await expect(page.getByText(/7% listened/)).toBeVisible();
  await page.getByRole('button', { name: 'Resume' }).click();

  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/play_media')?.args).toMatchObject({
    queue_id: WEB_PLAYER_ID, media: 'library://audiobook/book-1',
  });
  // Straight after starting it, the book is moved to where this listener was.
  await expect.poll(() => ma.sentCommands.filter((c) => c.command === 'player_queues/seek').at(-1)?.args?.position)
    .toBe(4321);

  const book = { title: 'Project Hail Mary', artist: 'Andy Weir', uri: 'library://audiobook/book-1', duration: 58320, position: 4400, volume: 70 };
  pushNowPlaying(ma, WEB_PLAYER_ID, book);
  await expect.poll(() => page.evaluate(() => navigator.mediaSession?.playbackState)).toBe('playing');

  // The phone goes to the background (or the screen off): saved at once.
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => saved.length).toBeGreaterThan(0);
  expect(saved[0]).toMatchObject({ item_id: 'book-1', duration: 58320 });
  expect(Number(saved[0].current_time)).toBeGreaterThanOrEqual(4321);
  api.assertNoUnmocked();
});
