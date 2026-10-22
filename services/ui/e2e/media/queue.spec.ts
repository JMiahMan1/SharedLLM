import { expect, test, type Locator, type Page } from '@playwright/test';
import { goToPage, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';

const NOW = {
  queue_item_id: 'q1',
  name: 'Midnight Drive',
  duration: 210,
  media_item: { artists: ['The Synthwave Collective'] },
};

const ITEMS = [
  NOW,
  { queue_item_id: 'q2', name: 'Neon Rain', duration: 180, media_item: { artists: ['Night Operator'] } },
  { queue_item_id: 'q3', name: 'Third Song', duration: 200, media_item: { artists: ['The Middle'] } },
  { queue_item_id: 'q4', name: 'Fourth Song', duration: 240, media_item: { artists: ['The Last'] } },
];

const QUEUE = {
  queue_id: 'hermetic-web-player',
  current_index: 0,
  current_item: NOW,
  shuffle_enabled: false,
  repeat_mode: 'off',
};

async function openQueue(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: 'Open queue' }).click();
  const sheet = page.getByTestId('queue-sheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.getByText('Third Song')).toBeVisible();
  return sheet;
}

async function dragHandle(page: Page, from: Locator, to: Locator): Promise<void> {
  const source = await from.boundingBox();
  const target = await to.boundingBox();
  if (!source || !target) throw new Error('Queue handle has no layout box');
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  // Exceed the 6px pointer activation distance before aiming at the target.
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2 - 12, { steps: 4 });
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 12 });
  await page.mouse.up();
}

test('drags a queued track ahead of the previous one', async ({ page }) => {
  const { ma } = await setupPlayerMocks(page, {
    ma: { queue: QUEUE, items: ITEMS },
  });
  await startTrack(page, ma);
  await goToPage(page, 'home');
  await page.getByTestId('mini-player').getByRole('button', { name: 'Open now playing' }).click();
  const queue = await openQueue(page);
  // Let the sheet's spring settle before measuring the row boxes.
  await page.waitForTimeout(350);

  await dragHandle(
    page,
    queue.getByRole('button', { name: 'Reorder Third Song' }),
    queue.getByRole('button', { name: 'Reorder Neon Rain' }),
  );

  await expect.poll(() => ma.sentCommands.find(
    (entry) => entry.command === 'player_queues/move_item',
  )).toBeTruthy();
  const move = ma.sentCommands.find((entry) => entry.command === 'player_queues/move_item');
  expect(move?.args).toMatchObject({
    queue_id: 'hermetic-web-player',
    queue_item_id: 'q3',
    pos_shift: -1,
  });
});

test('reorders the queue with the keyboard', async ({ page }) => {
  const { ma } = await setupPlayerMocks(page, {
    ma: { queue: QUEUE, items: ITEMS },
  });
  await startTrack(page, ma);
  await goToPage(page, 'home');
  await page.getByTestId('mini-player').getByRole('button', { name: 'Open now playing' }).click();
  const queue = await openQueue(page);

  const handle = queue.getByRole('button', { name: 'Reorder Third Song' });
  await handle.focus();
  await page.keyboard.press('Space');
  await expect(handle).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(200);
  await page.keyboard.press('ArrowUp');
  await page.waitForTimeout(200);
  await page.keyboard.press('Space');

  await expect.poll(() => ma.sentCommands.find(
    (entry) => entry.command === 'player_queues/move_item',
  )).toBeTruthy();
  const move = ma.sentCommands.find((entry) => entry.command === 'player_queues/move_item');
  expect(move?.args).toMatchObject({
    queue_item_id: 'q3',
    pos_shift: -1,
  });
});
