import { expect, test, type Page } from '@playwright/test';
import { expectNoA11yViolations } from '../fixtures/a11y';
import { clearFocus, pushPlaybackState, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';
import { WEB_PLAYER_ID } from '../fixtures/mediaMocks';

/** P7-T1: axe finds no serious or critical issue on any media screen or sheet. */

const ROUTES: Array<[string, string, (page: Page) => Promise<void>]> = [
  ['Listen Now', '/media', async (page) => { await expect(page.getByRole('heading', { name: 'Recently played' })).toBeVisible(); }],
  ['Search (empty)', '/media/search', async (page) => { await expect(page.getByRole('searchbox')).toBeVisible(); }],
  ['Search (results)', '/media/search?q=neon', async (page) => { await expect(page.getByRole('heading', { name: 'Top result' })).toBeVisible(); }],
  ['Library', '/media/library/tracks', async (page) => { await expect(page.getByRole('list', { name: 'Songs' })).toBeVisible(); }],
  ['Audiobook libraries', '/media/library/audiobooks', async (page) => { await expect(page.getByRole('heading', { name: 'Library' })).toBeVisible(); }],
  ['Favorites', '/media/library/favorites', async (page) => { await expect(page.getByRole('list', { name: 'Favorites' })).toBeVisible(); }],
  ['Album', '/media/album/library/1', async (page) => { await expect(page.getByRole('list', { name: 'Tracks' })).toBeVisible(); }],
  ['Playlist', '/media/playlist/library/1', async (page) => { await expect(page.getByRole('list', { name: 'Tracks' })).toBeVisible(); }],
  ['Book', '/media/book/book-1', async (page) => { await expect(page.getByRole('list', { name: 'Chapters' })).toBeVisible(); }],
  ['Artist', '/media/artist/library/1', async (page) => { await expect(page.getByRole('heading', { name: 'Neon Nights', level: 1 })).toBeVisible(); }],
  ['Podcast', '/media/podcast/pod-1', async (page) => { await expect(page.getByRole('list', { name: 'Episodes' })).toBeVisible(); }],
];

for (const [name, path, ready] of ROUTES) {
  test(`P7-T1 ${name} has no serious accessibility issues`, async ({ page }) => {
    await setupPlayerMocks(page);
    await page.goto(path);
    await ready(page);
    await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
    await expectNoA11yViolations(page);
  });
}

test('P7-T1 the open menus and sheets have no serious accessibility issues', async ({ page }) => {
  const { ma } = await setupPlayerMocks(page, {
    ma: {
      queue: { queue_id: WEB_PLAYER_ID, current_index: 0 },
      items: [
        { queue_item_id: 'q1', name: 'Midnight Drive', duration: 210 },
        { queue_item_id: 'q2', name: 'Neon Rain', duration: 187 },
      ],
    },
  });
  await startTrack(page, ma);
  pushPlaybackState(ma, 'paused');

  // The item menu and the output picker on Listen Now.
  await page.getByRole('button', { name: 'More for Neon Rain' }).first().click();
  await expect(page.getByRole('menu')).toBeVisible();
  await expectNoA11yViolations(page);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /^Playing on/ }).click();
  await expect(page.getByRole('dialog', { name: 'Playing on' })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.keyboard.press('Escape');

  // Now Playing, the queue, the output sheet and the shortcut help.
  await clearFocus(page);
  await page.keyboard.press('f');
  const sheet = page.getByTestId('now-playing-sheet');
  await expect(sheet.getByRole('heading', { name: 'Midnight Drive' })).toBeVisible();
  await expectNoA11yViolations(page);
  await sheet.getByRole('button', { name: 'Open queue' }).click();
  await expect(page.getByTestId('queue-sheet').getByText('Neon Rain')).toBeVisible();
  await expectNoA11yViolations(page);
  await page.keyboard.press('Escape');
  await sheet.getByRole('button', { name: 'Choose output' }).click();
  await expect(page.getByTestId('output-sheet')).toBeVisible();
  await expectNoA11yViolations(page);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await clearFocus(page);
  await page.keyboard.press('?');
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeVisible();
  await expectNoA11yViolations(page);
});
