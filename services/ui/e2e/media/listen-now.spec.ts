import { expect, test } from '@playwright/test';
import { setupPlayerMocks } from '../fixtures/mediaPlayback';
import { mockMediaApi, seedAuth, seedPlayerId, WEB_PLAYER_ID } from '../fixtures/mediaMocks';

const HOME_STATUS = {
  status: 'SUCCESS',
  message: 'ok',
  service: 'media',
  detail: {
    // Nothing "active", so the page does not switch to it on its own.
    active: null,
    available: [],
    all_players: [
      {
        entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'playing',
        media_title: 'Morning Jazz', media_artist: 'Trio', volume_level: 0.3, ma_player_id: 'kitchen',
      },
    ],
  },
};

test('P5-T1 shows every shelf and plays a recent track on this device (journey 1)', async ({ page }) => {
  const { api, ma } = await setupPlayerMocks(page);
  await page.goto('/media');
  await expect(page.getByRole('heading', { name: 'Listen Now', level: 1 })).toBeVisible();
  for (const shelf of ['Continue listening', 'Recently played', 'Your playlists', 'Favorites', 'Radio']) {
    await expect(page.getByRole('heading', { name: shelf, level: 2 })).toBeVisible();
  }
  // The book in progress carries its progress ring and time left.
  const continueShelf = page.getByRole('region', { name: 'Continue listening' });
  await expect(continueShelf.getByText('Project Hail Mary')).toBeVisible();
  await expect(continueShelf.getByRole('img', { name: '42% listened' })).toBeVisible();
  await expect(continueShelf.getByText('9h 23m left')).toBeVisible();

  await page.getByRole('region', { name: 'Recently played' }).getByRole('button', { name: 'Play Neon Rain' }).click();
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/play_media')?.args).toMatchObject({
    queue_id: WEB_PLAYER_ID, media: 'library://track/2',
  });
  api.assertNoUnmocked();
});

test('P5-T1 shows what plays around the house and takes control of it (journey 2)', async ({ page }) => {
  const { api } = await setupPlayerMocks(page, { mediaStatus: HOME_STATUS });
  await page.goto('/media');
  const strip = page.getByRole('region', { name: 'Playing in your home' });
  await expect(strip.getByText('Morning Jazz')).toBeVisible();
  await strip.getByRole('button', { name: /Control Kitchen Speaker/ }).click();
  await expect(page.getByRole('button', { name: /^Playing on Kitchen Speaker/ })).toBeVisible();
  // The card now drives the kitchen, and the strip no longer repeats it.
  await expect(page.getByText('Playing through Kitchen Speaker')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Playing in your home' })).toHaveCount(0);
  api.assertNoUnmocked();
});

for (const [scenario, notice, present, absent] of [
  ['ma-down', /Music Assistant did not answer/, 'Project Hail Mary', 'Late Night Synth'],
  ['abs-down', /Audiobookshelf did not answer/, 'Late Night Synth', 'Project Hail Mary'],
] as const) {
  test(`P5-T1 ${scenario}: the other service's shelves stay up with a notice (journey 11)`, async ({ page }) => {
    await seedAuth(page);
    await seedPlayerId(page);
    const api = await mockMediaApi(page, scenario);
    await page.goto('/media');
    await expect(page.getByText(notice).first()).toBeVisible();
    await expect(page.getByText(present).first()).toBeVisible();
    await expect(page.getByText(absent)).toHaveCount(0);
    api.assertNoUnmocked();
  });
}

test('P5-T1 an empty library says so instead of showing empty shelves', async ({ page }) => {
  await seedAuth(page);
  await seedPlayerId(page);
  const api = await mockMediaApi(page, 'empty-library');
  await page.goto('/media');
  await expect(page.getByText('Nothing here yet')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Recently played' })).toHaveCount(0);
  api.assertNoUnmocked();
});
