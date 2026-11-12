import { expect, test, type Page } from '@playwright/test';
import { goToPage, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';

/**
 * Watch `selector` on every animation frame from now on and record whether it
 * was ever displaced (a spring mid-flight). Started before the tap, so a
 * spring that has already landed by the time the test looks is still seen.
 */
async function watchMotion(page: Page, key: string, selector: string): Promise<void> {
  await page.evaluate(({ key, selector }) => {
    const w = window as unknown as Record<string, { moved: boolean; frames: number }>;
    const rec = { moved: false, frames: 0 };
    w[key] = rec;
    const tick = () => {
      const el = document.querySelector(selector);
      if (el) {
        rec.frames += 1;
        const t = getComputedStyle(el).transform;
        if (t && t !== 'none' && !/^matrix\(1, 0, 0, 1, 0, 0\)$/.test(t)) rec.moved = true;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, { key, selector });
}

const motion = (page: Page, key: string) =>
  page.evaluate((k) => (window as unknown as Record<string, { moved: boolean; frames: number }>)[k], key);

async function openNowPlaying(page: Page) {
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

  await watchMotion(page, '__npMotion', '[data-testid="now-playing-sheet"] div.max-w-3xl');
  const sheet = await openNowPlaying(page);
  await expect(sheet).toHaveAttribute('data-reduced-motion', 'true');
  // Every frame since the tap, a dozen at least: a spring would have slid the panel up.
  await expect.poll(async () => (await motion(page, '__npMotion')).frames).toBeGreaterThan(12);
  expect((await motion(page, '__npMotion')).moved).toBe(false);

  await watchMotion(page, '__queueMotion', '[data-testid="queue-sheet"]');
  await sheet.getByRole('button', { name: 'Open queue' }).click();
  const queue = page.getByTestId('queue-sheet');
  await expect(queue).toHaveAttribute('data-reduced-motion', 'true');
  await expect.poll(async () => (await motion(page, '__queueMotion')).frames).toBeGreaterThan(12);
  expect((await motion(page, '__queueMotion')).moved).toBe(false);
});

test('P6-T4 without it the sheets still animate', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const { ma } = await setupPlayerMocks(page);
  await startTrack(page, ma);
  await goToPage(page, 'home');
  await expect(page.getByRole('heading', { name: 'Jarvis Dashboard', level: 1 })).toBeVisible();
  await watchMotion(page, '__npMotion', '[data-testid="now-playing-sheet"] div.max-w-3xl');
  const sheet = await openNowPlaying(page);
  await expect(sheet).toHaveAttribute('data-reduced-motion', 'false');
  // The panel springs up from below.
  await expect.poll(async () => (await motion(page, '__npMotion')).moved).toBe(true);
});
