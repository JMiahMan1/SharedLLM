import { expect, test } from '@playwright/test';
import { setupPlayerMocks } from '../fixtures/mediaPlayback';

function libraryPage(offset: number, count: number) {
  return {
    tab: 'tracks', offset, limit: 50, libraries: [], errors: { ma: null, abs: null },
    items: Array.from({ length: count }, (_, i) => ({
      uri: `library://track/${offset + i}`, name: `Song ${offset + i}`, media_type: 'track', artist: 'Band', artist_uri: '',
      album: '', album_uri: '', image: '', duration: 180, version: '', favorite: null,
    })),
  };
}

test('P5-T3 the library keeps loading as it scrolls and only draws what is on screen', async ({ page }) => {
  const { api } = await setupPlayerMocks(page);
  const offsets: number[] = [];
  await page.route('**/api/media/library/tracks**', async (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
    offsets.push(offset);
    await route.fulfill({ json: libraryPage(offset, offset < 150 ? 50 : 20) });
  });
  await page.goto('/media/library/tracks');
  await expect(page.getByRole('button', { name: 'Play Song 0', exact: true })).toBeVisible();

  // Scrolling to the end of each page asks for the next one.
  for (const next of [50, 100, 150]) {
    await page.getByRole('button', { name: 'Load more' }).scrollIntoViewIfNeeded();
    await expect.poll(() => offsets).toContain(next);
  }
  await expect(page.getByRole('button', { name: 'Load more' })).toHaveCount(0);
  // 170 songs, but only the rows near the viewport are in the page.
  const rendered = await page.getByRole('list', { name: 'Songs' }).getByRole('listitem').count();
  expect(rendered).toBeGreaterThan(5);
  expect(rendered).toBeLessThan(100);
  api.assertNoUnmocked();
});

test('P5-T3 tabs are links, and Listen Now\'s See all lands on the right one', async ({ page }) => {
  const { api } = await setupPlayerMocks(page);
  await page.goto('/media');
  await page.getByRole('link', { name: 'See all your playlists' }).click();
  await expect(page).toHaveURL(/\/media\/library\/playlists$/);
  await expect(page.getByRole('link', { name: 'Playlists' })).toHaveAttribute('aria-current', 'page');
  await page.getByRole('link', { name: 'Favorites', exact: true }).click();
  await expect(page.getByRole('list', { name: 'Favorites' }).getByText('Midnight Drive')).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/media\/library\/playlists$/);
  api.assertNoUnmocked();
});
