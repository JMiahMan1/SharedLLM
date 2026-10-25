import { expect, test } from '@playwright/test';
import { pushPlaybackState, pushPosition, setupPlayerMocks } from '../fixtures/mediaPlayback';
import { pushNowPlaying, WEB_PLAYER_ID } from '../fixtures/mediaMocks';

test('P5-T5 resume a book, skip, change speed, save progress, jump to a chapter (journey 8)', async ({ page }) => {
  await page.clock.install();
  const { api, ma } = await setupPlayerMocks(page);
  const saved: Array<Record<string, unknown>> = [];
  await page.route('**/api/media/abs/progress', async (route) => {
    saved.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ json: { status: 'SUCCESS', item_id: 'book-1', episode_id: null, message: 'ok' } });
  });

  await page.goto('/media/book/book-1');
  await expect(page.getByRole('heading', { name: 'Project Hail Mary', level: 1 })).toBeVisible();
  await expect(page.getByText(/42% listened/)).toBeVisible();
  await page.getByRole('button', { name: 'Resume' }).click();
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/play_media')?.args).toMatchObject({
    queue_id: WEB_PLAYER_ID, media: 'library://audiobook/book-1',
  });
  const book = { title: 'Project Hail Mary', artist: 'Andy Weir', uri: 'library://audiobook/book-1', duration: 58320, position: 24494, volume: 70 };
  pushNowPlaying(ma, WEB_PLAYER_ID, book);
  // The browser player takes MA's clock while paused (playing, Sendspin runs it).
  pushPlaybackState(ma, 'paused', book);
  pushPosition(ma, 24494, book);

  await page.getByRole('button', { name: 'Forward 30 seconds' }).click();
  await expect.poll(() => ma.sentCommands.filter((c) => c.command === 'player_queues/seek').at(-1)?.args?.position)
    .toBe(24524);

  await page.getByRole('button', { name: 'Playback speed 1×' }).click();
  await page.getByRole('button', { name: 'Playback speed 1.25×' }).click();
  await expect(page.getByRole('button', { name: 'Playback speed 1.5×' })).toBeVisible();

  await page.clock.fastForward(15_000);
  await expect.poll(() => saved.length).toBeGreaterThan(0);
  expect(saved[0]).toMatchObject({ item_id: 'book-1', duration: 58320 });

  await page.getByRole('list', { name: 'Chapters' }).getByRole('button', { name: 'Play Chapter 2' }).click();
  await expect.poll(() => ma.sentCommands.filter((c) => c.command === 'player_queues/seek').at(-1)?.args?.position).toBe(1800);
  api.assertNoUnmocked();
});
