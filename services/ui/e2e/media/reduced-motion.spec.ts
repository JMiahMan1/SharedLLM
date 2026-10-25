import { expect, test, type Locator, type Page } from '@playwright/test';
import { goToPage, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';

/** Whether the animated panel is displaced right now (a spring mid-flight). */
async function moving(panel: Locator): Promise<boolean> {
  return panel.evaluate((el) => {
    const t = getComputedStyle(el).transform;
    return Boolean(t && t !== 'none' && !/^matrix\(1, 0, 0, 1, 0, 0\)$/.test(t));
  });
}

async function openNowPlaying(page: Page): Promise<Locator> {
  await page.getByTestId('mini-player').getByRole('button', { name: 'Open now playing' }).click();
  return page.getByTestId('now-playing-sheet');
}

test('P6-T4 with reduced motion the sheets open without moving', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  // The dashboard's own calls are aborted by the catch-all, never sent out.
  const { ma } = await setupPlayerMocks(page);
  await startTrack(page, ma);
  await goToPage(page, 'home');
  await expect(page.getByRole('heading', { name: 'Jarvis Dashboard', level: 1 })).toBeVisible();

  const sheet = await openNowPlaying(page);
  await expect(sheet).toHaveAttribute('data-reduced-motion', 'true');
  // Sampled at once: a spring would still be sliding the panel up.
  expect(await moving(sheet.locator('div.max-w-3xl').first())).toBe(false);

  await sheet.getByRole('button', { name: 'Open queue' }).click();
  const queue = page.getByTestId('queue-sheet');
  await expect(queue).toHaveAttribute('data-reduced-motion', 'true');
  expect(await moving(queue)).toBe(false);
});

test('P6-T4 without it the sheets still animate', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const { ma } = await setupPlayerMocks(page);
  await startTrack(page, ma);
  await goToPage(page, 'home');
  await expect(page.getByRole('heading', { name: 'Jarvis Dashboard', level: 1 })).toBeVisible();
  const sheet = await openNowPlaying(page);
  await expect(sheet).toHaveAttribute('data-reduced-motion', 'false');
  // The panel springs up from below.
  expect(await moving(sheet.locator('div.max-w-3xl').first())).toBe(true);
});
