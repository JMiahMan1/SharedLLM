import { expect, test } from '@playwright/test';
import { clearFocus, goToPage, pushPlaybackState, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';

test('drives the player with single-key shortcuts', async ({ page }) => {
  const queueState: Record<string, unknown> = {
    queue_id: 'hermetic-web-player',
    shuffle_enabled: false,
    repeat_mode: 'off',
  };
  const { ma } = await setupPlayerMocks(page, { ma: { queue: queueState } });
  await startTrack(page, ma);
  await goToPage(page, 'home');
  await clearFocus(page);

  const has = (command: string) => ma.sentCommands.some((entry) => entry.command === command);

  await page.keyboard.press('Space');
  await expect.poll(() => has('players/cmd/pause')).toBe(true);
  pushPlaybackState(ma, 'paused');
  await expect(page.getByTestId('mini-player').getByRole('button', { name: 'Play', exact: true })).toBeVisible();

  await page.keyboard.press('Space');
  await expect.poll(() => has('players/cmd/play')).toBe(true);
  // Stay paused so the app's playback clock cannot move the seek origin
  // between the key press and the assertion.
  pushPlaybackState(ma, 'paused');

  const seeks = () => ma.sentCommands.filter((entry) => entry.command === 'player_queues/seek');
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => seeks().length).toBe(1);
  const firstSeek = Number(seeks()[0].args?.position);

  await page.keyboard.press('Shift+ArrowRight');
  await expect.poll(() => seeks().length).toBe(2);
  // Shift is the same action with a 30-second step instead of 5.
  expect(Number(seeks()[1].args?.position)).toBe(firstSeek + 30);

  await clearFocus(page);
  await page.keyboard.press('f');
  const nowPlaying = page.getByTestId('now-playing-sheet');
  await expect(nowPlaying).toBeVisible();
  const volume = nowPlaying.getByRole('slider', { name: 'Volume' });
  const initialVolume = Number(await volume.inputValue());
  await page.keyboard.press('ArrowUp');
  await expect(volume).toHaveValue(String(Math.min(100, initialVolume + 5)));
  await page.keyboard.press('m');
  await expect(nowPlaying.getByRole('button', { name: 'Unmute' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(nowPlaying).toBeHidden();

  await clearFocus(page);
  await page.keyboard.press('m');
  await page.keyboard.press('n');
  await expect.poll(() => has('player_queues/next')).toBe(true);
  await page.keyboard.press('p');
  await expect.poll(() => has('player_queues/previous')).toBe(true);

  await page.keyboard.press('s');
  await expect.poll(() => ma.sentCommands.find(
    (entry) => entry.command === 'player_queues/shuffle',
  )).toBeTruthy();
  expect(ma.sentCommands.find((entry) => entry.command === 'player_queues/shuffle')?.args)
    .toMatchObject({ shuffle_enabled: true });

  await page.keyboard.press('r');
  await expect.poll(() => ma.sentCommands.some(
    (entry) => entry.command === 'player_queues/repeat' && entry.args?.repeat_mode === 'all',
  )).toBe(true);
  queueState.repeat_mode = 'all';
  await page.keyboard.press('r');
  await expect.poll(() => ma.sentCommands.some(
    (entry) => entry.command === 'player_queues/repeat' && entry.args?.repeat_mode === 'one',
  )).toBe(true);

  await page.keyboard.press('q');
  await expect(page.getByTestId('queue-sheet')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('queue-sheet')).toBeHidden();

  await page.keyboard.press('?');
  const help = page.getByTestId('shortcut-help');
  await expect(help).toBeVisible();
  await expect(help.getByRole('heading', { name: 'Keyboard shortcuts' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(help).toBeHidden();
});

test('leaves shortcuts alone while typing in search', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) < 768, 'The header search is desktop-only');
  const { ma } = await setupPlayerMocks(page);
  await startTrack(page, ma);
  await goToPage(page, 'home');
  await clearFocus(page);

  const pauseBefore = ma.sentCommands.filter((entry) => entry.command === 'players/cmd/pause').length;
  const nextBefore = ma.sentCommands.filter((entry) => entry.command === 'player_queues/next').length;

  await page.keyboard.press('/');
  const search = page.getByRole('textbox', { name: 'Search pages, tabs and settings' });
  await expect(search).toBeFocused();
  await search.fill('cal');
  await page.keyboard.press('Space');
  await page.keyboard.press('n');
  await page.keyboard.press('?');

  await expect(search).toHaveValue('cal n?');
  await expect(page.getByTestId('shortcut-help')).toBeHidden();
  expect(ma.sentCommands.filter((entry) => entry.command === 'players/cmd/pause')).toHaveLength(pauseBefore);
  expect(ma.sentCommands.filter((entry) => entry.command === 'player_queues/next')).toHaveLength(nextBefore);
});
