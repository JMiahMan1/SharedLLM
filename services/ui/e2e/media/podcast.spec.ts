import { expect, test } from '@playwright/test';
import { setupPlayerMocks } from '../fixtures/mediaPlayback';
import { WEB_PLAYER_ID } from '../fixtures/mediaMocks';

test('P5-T6 open a podcast from search and play the latest episode (journey 9)', async ({ page }) => {
  const { api, ma } = await setupPlayerMocks(page);
  await page.route('**/api/media/item**', async (route) => {
    const uri = new URL(route.request().url()).searchParams.get('uri');
    if (uri !== 'abs://pod-1') return route.fallback();
    await route.fulfill({ json: {
      uri, name: 'The Quiet Hour', media_type: 'podcast', artist: 'Ada Lane', album: '', image: '/api/items/pod-1/cover',
      duration: null, description: 'Slow conversations.', children: [], errors: { ma: null, abs: null },
    } });
  });
  await page.goto('/media/search?q=quiet');
  await page.getByRole('region', { name: 'Podcasts' }).getByRole('link', { name: /The Quiet Hour/ }).click();
  await expect(page).toHaveURL(/\/media\/podcast\/pod-1$/);
  await expect(page.getByRole('heading', { name: 'The Quiet Hour', level: 1 })).toBeVisible();
  const episodes = page.getByRole('list', { name: 'Episodes' }).getByRole('listitem');
  await expect(episodes).toHaveCount(2);
  await expect(episodes.first()).toContainText('Episode 2: Slow Mornings');
  await expect(episodes.nth(1)).toContainText('Played');

  await page.getByRole('button', { name: 'Play latest' }).click();
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/play_media')?.args).toMatchObject({
    queue_id: WEB_PLAYER_ID, media: 'audiobookshelf--abs1://podcast_episode/pod-1 ep-2',
  });
  api.assertNoUnmocked();
});
