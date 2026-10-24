import { expect, test } from '@playwright/test';
import { goToPage, setupPlayerMocks, startTrack } from '../fixtures/mediaPlayback';

const OUTPUT_STATUS = {
  status: 'SUCCESS',
  message: 'ok',
  service: 'media',
  detail: {
    active: null,
    available: [],
    all_players: [
      {
        entity_id: 'media_player.kitchen',
        friendly_name: 'Kitchen Speaker',
        state: 'playing',
        media_title: 'Midnight Drive',
        ma_player_id: 'kitchen',
        volume_level: 0.42,
      },
      {
        entity_id: 'media_player.office',
        friendly_name: 'Office Speaker',
        state: 'idle',
        ma_player_id: 'office',
        volume_level: 0.2,
      },
      { entity_id: 'media_player.den_tv', friendly_name: 'Den TV', state: 'idle', icon_kind: 'tv' },
    ],
  },
};

async function openOutputPicker(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Choose output' }).click();
  const sheet = page.getByTestId('output-sheet');
  await expect(sheet).toBeVisible();
  return sheet;
}

test('moves playback to one de-duplicated speaker', async ({ page }) => {
  const { ma } = await setupPlayerMocks(page, {
    mediaStatus: OUTPUT_STATUS,
    ma: {
      players: [
        // Same MA player as the Home Assistant row above: it must not appear
        // twice just because both integrations know about it.
        { player_id: 'kitchen', name: 'Kitchen', state: 'playing', available: true, volume_level: 42 },
      ],
    },
  });
  await startTrack(page, ma);
  await goToPage(page, 'home');

  await page.getByTestId('mini-player').getByRole('button', { name: 'Open now playing' }).click();
  const output = await openOutputPicker(page);

  await expect(output.getByRole('heading', { name: 'Speakers' })).toBeVisible();
  await expect(output.getByRole('heading', { name: 'TVs' })).toBeVisible();
  await expect(output.getByText('Kitchen Speaker', { exact: true })).toHaveCount(1);
  await expect(output.getByText('Office Speaker', { exact: true })).toHaveCount(1);
  await expect(output.getByText('Den TV', { exact: true })).toHaveCount(1);

  await output.getByRole('button', { name: 'Play here on Kitchen Speaker' }).click();

  await expect.poll(() => ma.sentCommands.find(
    (entry) => entry.command === 'player_queues/transfer',
  )).toBeTruthy();
  const transfer = ma.sentCommands.find((entry) => entry.command === 'player_queues/transfer');
  expect(transfer?.args).toMatchObject({
    source_queue_id: 'hermetic-web-player',
    target_queue_id: 'kitchen',
  });
  await expect.poll(() => ma.sentCommands.some(
    (entry) => entry.command === 'players/cmd/pause',
  )).toBe(true);

  await expect(page.getByTestId('output-sheet')).toBeHidden();
  await expect(page.getByTestId('now-playing-sheet')).toBeHidden();
});

test('groups a speaker and changes its volume from the picker', async ({ page }) => {
  const { ma, remote } = await setupPlayerMocks(page, {
    mediaStatus: OUTPUT_STATUS,
    ma: { players: [] },
    // Music Assistant says Office can play in sync with this device; Kitchen cannot.
    remotePlayers: [
      { player_id: 'hermetic-web-player', name: 'Web Player', available: true, can_group_with: ['office'] },
      { player_id: 'kitchen', name: 'Kitchen Speaker', available: true, state: 'playing', volume_level: 42 },
      { player_id: 'office', name: 'Office Speaker', available: true, state: 'idle', volume_level: 20 },
    ],
  });
  await startTrack(page, ma);
  await goToPage(page, 'home');

  await page.getByTestId('mini-player').getByRole('button', { name: 'Open now playing' }).click();
  const output = await openOutputPicker(page);

  await expect(output.getByRole('checkbox', { name: /Group Kitchen Speaker/ })).toHaveCount(0);
  // A controlled checkbox stays unchecked until MA reports the new group;
  // click (rather than check) so the command, not the fixture echo, is asserted.
  await output.getByRole('checkbox', { name: 'Group Office Speaker with This device' }).click();
  await expect.poll(() => remote.sentCommands.find(
    (entry) => entry.command === 'players/cmd/set_members',
  )).toBeTruthy();
  const group = remote.sentCommands.find((entry) => entry.command === 'players/cmd/set_members');
  expect(group?.args).toEqual({ target_player: 'hermetic-web-player', player_ids_to_add: ['office'], player_ids_to_remove: [] });

  const volume = output.getByRole('slider', { name: 'Volume for Kitchen Speaker' });
  await volume.fill('55');
  await expect.poll(() => remote.sentCommands.find(
    (entry) => entry.command === 'players/cmd/volume_set',
  )).toBeTruthy();
  const volumeSet = remote.sentCommands.find((entry) => entry.command === 'players/cmd/volume_set');
  expect(volumeSet?.args).toMatchObject({ player_id: 'kitchen', volume_level: 55 });
});
