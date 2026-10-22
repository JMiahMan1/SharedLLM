import { expect, test } from '@playwright/test';
import { goToPage, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';

async function openSheetFromMiniPlayer(page: import('@playwright/test').Page) {
  await page.getByTestId('mini-player').getByRole('button', { name: 'Open now playing' }).click();
  const sheet = page.getByTestId('now-playing-sheet');
  await expect(sheet).toBeVisible();
  return sheet;
}

test('opens from the mini player and commits one seek on release', async ({ page }) => {
  const { ma } = await setupPlayerMocks(page);
  await startTrack(page, ma);
  await goToPage(page, 'home');

  const sheet = await openSheetFromMiniPlayer(page);
  await expect(page.getByRole('dialog', { name: 'Now playing' })).toBeVisible();
  await expect(sheet.getByText('Midnight Drive')).toBeVisible();
  await expect(sheet.getByText('The Synthwave Collective')).toBeVisible();

  const slider = sheet.getByRole('slider', { name: 'Playback position' });
  await slider.scrollIntoViewIfNeeded();
  const box = await slider.boundingBox();
  expect(box).not.toBeNull();

  const before = ma.sentCommands.filter((entry) => entry.command === 'player_queues/seek').length;
  await page.mouse.move(box!.x + box!.width * 0.2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width * 0.62, box!.y + box!.height / 2, { steps: 8 });
  const releasePosition = Number(await slider.inputValue());
  await page.mouse.up();

  await expect.poll(() => ma.sentCommands.filter(
    (entry) => entry.command === 'player_queues/seek',
  ).length).toBe(before + 1);
  const seeks = ma.sentCommands.filter((entry) => entry.command === 'player_queues/seek');
  expect(seeks.at(-1)?.args).toMatchObject({
    queue_id: 'hermetic-web-player',
    position: releasePosition,
  });

  await page.keyboard.press('Escape');
  await expect(sheet).toBeHidden();
});

test('swipe up on the mini player opens the sheet', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'the swipe gesture is for touch layouts');
  const { ma } = await setupPlayerMocks(page);
  await startTrack(page, ma);
  await goToPage(page, 'home');

  const mini = page.getByTestId('mini-player');
  const box = await mini.boundingBox();
  expect(box).not.toBeNull();
  const x = box!.x + box!.width / 2;
  await page.mouse.move(x, box!.y + box!.height * 0.7);
  await page.mouse.down();
  await page.mouse.move(x, box!.y + box!.height * 0.7 - 90, { steps: 12 });
  await page.mouse.up();

  await expect(page.getByTestId('now-playing-sheet')).toBeVisible();
});

test('browser back closes the sheet without leaving the page', async ({ page }) => {
  const { ma } = await setupPlayerMocks(page);
  await startTrack(page, ma);
  await goToPage(page, 'home');

  const sheet = await openSheetFromMiniPlayer(page);
  await page.evaluate(() => window.history.back());
  await expect(sheet).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Jarvis Dashboard', level: 1 })).toBeVisible();
});
