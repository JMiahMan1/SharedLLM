import { expect, test, type Page } from '@playwright/test';
import { setupPlayerMocks } from '../fixtures/mediaPlayback';
import { pushNowPlaying, WEB_PLAYER_ID } from '../fixtures/mediaMocks';

/**
 * P7-T2: the whole media journey with the keyboard alone. Every step moves
 * focus with Tab / Shift+Tab or a shortcut and acts with Enter, Space or the
 * arrows; the mouse is never used.
 */

const NOW = { queue_item_id: 'q1', name: 'Midnight Drive', duration: 210 };
const ITEMS = [
  NOW,
  { queue_item_id: 'q2', name: 'Neon Rain', duration: 180 },
  { queue_item_id: 'q3', name: 'Third Song', duration: 200 },
];

/** Press Tab (or Shift+Tab) until the focused element's accessible name matches. */
async function tabTo(page: Page, name: RegExp | string, opts: { back?: boolean; max?: number } = {}) {
  const want = typeof name === 'string' ? new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`) : name;
  for (let i = 0; i < (opts.max ?? 120); i += 1) {
    await page.keyboard.press(opts.back ? 'Shift+Tab' : 'Tab');
    const label = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return '';
      return el.getAttribute('aria-label') || (el.textContent || '').trim();
    });
    if (want.test(label)) return;
  }
  throw new Error(`Tab never reached ${String(name)}`);
}

test('P7-T2 play, open Now Playing, seek, reorder the queue and change output by keyboard alone', async ({ page }) => {
  const { ma, api } = await setupPlayerMocks(page, {
    ma: { queue: { queue_id: WEB_PLAYER_ID, current_index: 0, current_item: NOW }, items: ITEMS },
    mediaStatus: {
      status: 'SUCCESS', message: 'ok', service: 'media',
      detail: { active: null, available: [], all_players: [
        { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Speaker', state: 'idle', ma_player_id: 'kitchen' },
      ] },
    },
  });
  await page.goto('/media');
  await expect(page.getByRole('heading', { name: 'Recently played' })).toBeVisible();

  // Reach a track and play it.
  await tabTo(page, 'Play Midnight Drive');
  await page.keyboard.press('Enter');
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/play_media')?.args)
    .toMatchObject({ queue_id: WEB_PLAYER_ID, media: 'library://track/1' });
  pushNowPlaying(ma, WEB_PLAYER_ID, { title: 'Midnight Drive', artist: 'The Synthwave Collective', uri: 'library://track/1', duration: 210, position: 30 });
  // The card shows the title and Pause once MA's events are in; until then a
  // shortcut has nothing to act on.
  await expect(page.getByRole('button', { name: 'Pause' }).first()).toBeVisible();

  // Open Now Playing with its shortcut (focus is on a button, not a field).
  await page.keyboard.press('f');
  const sheet = page.getByTestId('now-playing-sheet');
  await expect(sheet.getByRole('heading', { name: 'Midnight Drive' })).toBeVisible();

  // Seek: Tab to the scrubber, step forward.
  await tabTo(page, 'Playback position');
  const seeksBefore = ma.sentCommands.filter((c) => c.command === 'player_queues/seek').length;
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => ma.sentCommands.filter((c) => c.command === 'player_queues/seek').length).toBe(seeksBefore + 1);

  // Reorder the queue: open it, pick up a handle, move it up, drop it.
  await tabTo(page, 'Open queue');
  await page.keyboard.press('Enter');
  const queue = page.getByTestId('queue-sheet');
  await expect(queue.getByText('Third Song')).toBeVisible();
  await tabTo(page, 'Reorder Third Song');
  const handle = queue.getByRole('button', { name: 'Reorder Third Song' });
  await page.keyboard.press('Space');
  await expect(handle).toHaveAttribute('aria-pressed', 'true');
  // dnd-kit only takes the arrow once it has measured the list after the
  // pick-up: press it until the row has visibly moved up, then drop.
  const rowShift = () => handle.evaluate((el) => {
    const row = el.closest('li') ?? el.parentElement!;
    return new DOMMatrixReadOnly(getComputedStyle(row).transform).m42;
  });
  await expect.poll(async () => {
    if ((await rowShift()) >= 0) await page.keyboard.press('ArrowUp');
    return rowShift();
  }).toBeLessThan(0);
  // …and until dnd-kit has settled the new drop target (it announces it).
  await expect(page.getByText(/was moved over droppable area/).first()).toBeAttached();
  await page.keyboard.press('Space');
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/move_item')?.args)
    .toMatchObject({ queue_item_id: 'q3', pos_shift: -1 });
  await page.keyboard.press('Escape');
  await expect(queue).toBeHidden();

  // Change output: the output sheet, then move playback to the kitchen.
  await tabTo(page, 'Choose output');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('output-sheet')).toBeVisible();
  await tabTo(page, 'Play here on Kitchen Speaker');
  await page.keyboard.press('Enter');
  await expect.poll(() => ma.sentCommands.find((c) => c.command === 'player_queues/transfer')?.args)
    .toMatchObject({ source_queue_id: WEB_PLAYER_ID, target_queue_id: 'kitchen' });
  api.assertNoUnmocked();
});
