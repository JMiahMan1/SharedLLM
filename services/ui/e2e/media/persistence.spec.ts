import { expect, test } from '@playwright/test';
import { goToPage, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';

/**
 * P4-T10: leaving /media must not tear down the player. The real failure was
 * a reload or remount closing both sockets, so this checks behaviour rather
 * than internal state: keep playing, navigate away, drive it from the
 * mini-player, and come back to the same track on one connection.
 */
test('keeps the browser player alive across pages', async ({ page }) => {
  const { ma, sendspin } = await setupPlayerMocks(page);
  await startTrack(page, ma);

  await goToPage(page, 'home');
  await expect(page.getByRole('heading', { name: 'Jarvis Dashboard', level: 1 })).toBeVisible();

  const mini = page.getByTestId('mini-player');
  await expect(mini).toBeVisible();
  await expect(mini.getByText('Midnight Drive')).toBeVisible();
  await expect(mini.getByRole('button', { name: 'Pause' })).toBeVisible();

  const beforeNext = ma.sentCommands.length;
  await mini.getByRole('button', { name: 'Next track' }).click();
  await expect.poll(() => ma.sentCommands.length).toBeGreaterThan(beforeNext);
  const next = ma.sentCommands.slice(beforeNext).find((entry) => entry.command === 'player_queues/next');
  expect(next?.args).toMatchObject({ queue_id: 'hermetic-web-player' });

  await goToPage(page, 'calendar');
  await expect(page).toHaveURL(/\/calendar$/);
  await expect(page.getByTestId('mini-player')).toBeVisible();

  await goToPage(page, 'media');
  await expect(page.getByRole('heading', { name: 'Media', level: 1 })).toBeVisible();
  await expect(page.getByText('Playing on this device')).toBeVisible();
  await expect(page.getByText('Midnight Drive').first()).toBeVisible();
  await expect(page.getByText('The Synthwave Collective').first()).toBeVisible();

  // One connection from the play click, still open after three page changes.
  expect(sendspin.connections).toBe(1);
  expect(sendspin.closes).toBe(0);
});
