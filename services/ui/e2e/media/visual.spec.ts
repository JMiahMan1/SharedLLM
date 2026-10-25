import { expect, test, type Page } from '@playwright/test';
import { clearFocus, pushPlaybackState, pushPosition, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';
import { WEB_PLAYER_ID } from '../fixtures/mediaMocks';

/**
 * P6-T3: the media screens under two theme packs (the default dark one and a
 * light one), on desktop and phone. Fixtures are fixed, the clock is frozen,
 * animations are off and web fonts are blocked (the platform's fallback font
 * is stable; Google Fonts over the network is not). Baselines live next to
 * this file; regenerate with `--update-snapshots` after an intended change.
 */

const THEMES = ['aurora', 'clean-athletic'] as const;

const QUEUE_ITEMS = [
  { queue_item_id: 'q1', name: 'Midnight Drive', duration: 210, media_item: { name: 'Midnight Drive', artists: [{ name: 'The Synthwave Collective' }] } },
  { queue_item_id: 'q2', name: 'Neon Rain', duration: 187, media_item: { name: 'Neon Rain', artists: [{ name: 'Vector Hold' }] } },
  { queue_item_id: 'q3', name: 'Chrome Horizon', duration: 243, media_item: { name: 'Chrome Horizon', artists: [{ name: 'The Synthwave Collective' }] } },
];

const STATUS = {
  status: 'SUCCESS', message: 'ok', service: 'media',
  detail: {
    active: null, available: [],
    all_players: [
      { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'idle', ma_player_id: 'kitchen', volume_level: 0.4 },
      { entity_id: 'media_player.den_tv', friendly_name: 'Den TV', state: 'idle', icon_kind: 'tv' },
    ],
  },
};

async function prepare(page: Page, theme: string) {
  await page.clock.setFixedTime(new Date('2026-10-24T12:00:00Z'));
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort());
  const mocks = await setupPlayerMocks(page, {
    mediaStatus: STATUS,
    ma: { queue: { queue_id: WEB_PLAYER_ID, current_index: 0, shuffle_enabled: false, repeat_mode: 'off' }, items: QUEUE_ITEMS },
    remotePlayers: [{ player_id: 'kitchen', name: 'Kitchen Speaker', available: true, state: 'idle', volume_level: 40 }],
  });
  await page.route('**/api/users/me/theme', (route) => route.fulfill({ json: { theme_id: theme, packs: [] } }));
  return mocks;
}

async function settle(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
}

const shot = { animations: 'disabled' as const, caret: 'hide' as const, maxDiffPixelRatio: 0.01 };

for (const theme of THEMES) {
  test.describe(`P6-T3 media visuals, ${theme}`, () => {
    test('Listen Now', async ({ page }) => {
      await prepare(page, theme);
      await page.goto('/media');
      await expect(page.getByRole('heading', { name: 'Recently played' })).toBeVisible();
      await settle(page);
      await expect(page).toHaveScreenshot(`listen-now-${theme}.png`, shot);
    });

    test('Search', async ({ page }) => {
      await prepare(page, theme);
      await page.goto('/media/search?q=neon');
      await expect(page.getByRole('heading', { name: 'Top result' })).toBeVisible();
      await settle(page);
      await expect(page).toHaveScreenshot(`search-${theme}.png`, shot);
    });

    test('Album', async ({ page }) => {
      await prepare(page, theme);
      await page.goto('/media/album/library/1');
      await expect(page.getByRole('list', { name: 'Tracks' })).toBeVisible();
      await settle(page);
      await expect(page).toHaveScreenshot(`album-${theme}.png`, shot);
    });

    test('Book', async ({ page }) => {
      await prepare(page, theme);
      await page.goto('/media/book/book-1');
      await expect(page.getByRole('list', { name: 'Chapters' })).toBeVisible();
      await expect(page.getByText(/42% listened/)).toBeVisible();
      await settle(page);
      await expect(page).toHaveScreenshot(`book-${theme}.png`, shot);
    });

    test('Now Playing, Queue and Output', async ({ page }) => {
      const { ma } = await prepare(page, theme);
      await startTrack(page, ma, { position: 42 });
      // Paused at a fixed point, so the clock in the shot does not move.
      pushPlaybackState(ma, 'paused');
      pushPosition(ma, 42);
      // Stay on the fully mocked media page (another page's unmocked calls
      // would raise retry toasts) and open Now Playing with its shortcut.
      await clearFocus(page);
      await page.keyboard.press('f');
      const sheet = page.getByTestId('now-playing-sheet');
      await expect(sheet.getByRole('heading', { name: 'Midnight Drive' })).toBeVisible();
      await settle(page);
      await expect(page).toHaveScreenshot(`now-playing-${theme}.png`, shot);

      await sheet.getByRole('button', { name: 'Open queue' }).click();
      const queue = page.getByTestId('queue-sheet');
      await expect(queue.getByText('Chrome Horizon')).toBeVisible();
      await expect(page).toHaveScreenshot(`queue-${theme}.png`, shot);
      await page.keyboard.press('Escape');
      await expect(queue).toBeHidden();

      await sheet.getByRole('button', { name: 'Choose output' }).click();
      const output = page.getByTestId('output-sheet');
      await expect(output.getByText('Kitchen Speaker', { exact: true })).toBeVisible();
      await expect(page).toHaveScreenshot(`output-${theme}.png`, shot);
    });
  });
}
