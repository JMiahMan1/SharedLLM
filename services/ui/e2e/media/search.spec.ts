import { expect, test } from '@playwright/test';
import { setupPlayerMocks } from '../fixtures/mediaPlayback';
import { WEB_PLAYER_ID } from '../fixtures/mediaMocks';

test('P5-T2 search groups results and Enter plays the top result (journey 3)', async ({ page }) => {
  const { api, ma } = await setupPlayerMocks(page);
  await page.goto('/media/search');
  const box = page.getByRole('searchbox', { name: 'Search music and books' });
  await expect(box).toBeFocused();
  await box.fill('midnight');
  await expect(page.getByRole('heading', { name: 'Top result' })).toBeVisible();
  for (const group of ['Songs', 'Artists', 'Albums', 'Playlists', 'Audiobooks', 'Podcasts', 'Authors']) {
    await expect(page.getByRole('heading', { name: group, level: 2 })).toBeVisible();
  }
  await box.press('Enter');
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/play_media')?.args).toMatchObject({
    queue_id: WEB_PLAYER_ID, media: 'library://track/1',
  });
  api.assertNoUnmocked();
});

test('P5-T2 UX-42 the query and filter survive back and forward', async ({ page }) => {
  const { api } = await setupPlayerMocks(page);
  await page.goto('/media');
  await page.goto('/media/search?q=neon');
  await expect(page.getByRole('heading', { name: 'Top result' })).toBeVisible();
  await page.getByRole('button', { name: 'Albums', exact: true }).click();
  await expect(page).toHaveURL(/type=albums/);
  await page.goBack();
  await expect(page).toHaveURL(/\/media\/search\?q=neon$/);
  await expect(page.getByRole('searchbox', { name: 'Search music and books' })).toHaveValue('neon');
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Listen Now' })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('searchbox', { name: 'Search music and books' })).toHaveValue('neon');
  api.assertNoUnmocked();
});

test('P5-T2 ⌘K from Listen Now opens search', async ({ page }) => {
  const { api } = await setupPlayerMocks(page);
  await page.goto('/media');
  await expect(page.getByRole('heading', { name: 'Listen Now' })).toBeVisible();
  await page.keyboard.press('ControlOrMeta+k');
  await expect(page).toHaveURL(/\/media\/search$/);
  await expect(page.getByRole('searchbox', { name: 'Search music and books' })).toBeFocused();
  api.assertNoUnmocked();
});
