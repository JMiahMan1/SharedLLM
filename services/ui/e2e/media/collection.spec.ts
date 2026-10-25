import { expect, test } from '@playwright/test';
import { setupPlayerMocks } from '../fixtures/mediaPlayback';
import { WEB_PLAYER_ID } from '../fixtures/mediaMocks';

test('P5-T4 an album opens from search, and Shuffle fills the queue shuffled (journey 4)', async ({ page }) => {
  const { api, ma } = await setupPlayerMocks(page);
  await page.goto('/media/search?q=neon');
  await page.getByRole('region', { name: 'Albums' }).getByRole('link', { name: /Neon Nights/ }).click();
  await expect(page).toHaveURL(/\/media\/album\/library\/1$/);
  await expect(page.getByRole('heading', { name: 'Neon Nights', level: 1 })).toBeVisible();
  await expect(page.getByRole('list', { name: 'Tracks' }).getByRole('listitem')).toHaveCount(3);

  await page.getByRole('button', { name: 'Shuffle' }).click();
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/play_media')?.args).toMatchObject({
    queue_id: WEB_PLAYER_ID, media: 'library://album/1',
  });
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/shuffle')?.args).toEqual({
    queue_id: WEB_PLAYER_ID, shuffle_enabled: true,
  });
  // Shuffle follows the queue being filled.
  const order = ma.sentCommands.map((c) => c.command);
  expect(order.indexOf('player_queues/shuffle')).toBeGreaterThan(order.indexOf('player_queues/play_media'));
  api.assertNoUnmocked();
});

test('P5-T4 Go to artist from a track menu opens the artist', async ({ page }) => {
  const { api } = await setupPlayerMocks(page);
  await page.route('**/api/media/item**', async (route) => {
    const uri = new URL(route.request().url()).searchParams.get('uri');
    if (uri === 'library://artist/1') {
      await route.fulfill({ json: {
        uri, name: 'The Synthwave Collective', media_type: 'artist', artist: '', album: '', image: '', duration: null, description: '',
        children: [{ uri: 'library://album/1', name: 'Neon Nights', media_type: 'album', artist: '', artist_uri: '', album: '', album_uri: '', image: '', duration: null, index: null }],
        errors: { ma: null, abs: null },
      } });
      return;
    }
    await route.fallback();
  });
  await page.goto('/media/album/library/1');
  await page.getByRole('button', { name: 'More for Midnight Drive' }).click();
  await page.getByRole('menuitem', { name: 'Go to artist' }).click();
  await expect(page).toHaveURL(/\/media\/artist\/library\/1$/);
  await expect(page.getByRole('heading', { name: 'The Synthwave Collective', level: 1 })).toBeVisible();
  await expect(page.getByRole('link', { name: /Neon Nights/ })).toBeVisible();
  api.assertNoUnmocked();
});
