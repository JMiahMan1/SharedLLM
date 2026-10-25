import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import OutputSheet from './OutputSheet';
import { MediaPlayerContext } from '../../../context/webPlayerContext';
import type { SharedPlayer } from '../../../context/webPlayerContext';
import { sendMaRemoteCommand } from '../../../lib/maRemotePlayer';

vi.mock('../../../services/api', () => ({
  api: {
    mediaStatus: vi.fn(),
    mediaTransport: vi.fn().mockResolvedValue({ status: 'SUCCESS' }),
  },
}));

vi.mock('../../../lib/maRemotePlayer', () => ({
  sendMaRemoteCommand: vi.fn().mockResolvedValue([]),
}));

import { api } from '../../../services/api';

function player(over: Partial<SharedPlayer> = {}): SharedPlayer {
  return {
    playerId: 'web-1',
    mediaTitle: 'A Song',
    mediaUri: 'library://track/1',
    isPlaying: true,
    volume: 70,
    muted: false,
    pause: vi.fn().mockResolvedValue(undefined),
    maCommand: vi.fn().mockResolvedValue({}),
    ...over,
  } as unknown as SharedPlayer;
}

const KITCHEN = {
  entity_id: 'media_player.kitchen',
  friendly_name: 'Kitchen Speaker',
  state: 'playing',
  media_title: 'Kitchen Song',
  volume_level: 0.42,
  ma_player_id: 'kitchen',
  group_members: [] as string[],
};
const OFFICE = {
  entity_id: 'media_player.office',
  friendly_name: 'Office Speaker',
  state: 'idle',
  volume_level: 0.2,
  ma_player_id: 'office',
  group_members: [] as string[],
};
const TV = {
  entity_id: 'media_player.loft_tv',
  friendly_name: 'Loft TV',
  state: 'idle',
  icon_kind: 'tv',
  volume_level: 0.3,
  ma_player_id: null,
  group_members: [] as string[],
};

function status(players = [KITCHEN, OFFICE, TV]) {
  return {
    status: 'SUCCESS',
    detail: { active: KITCHEN, all_players: players },
  };
}

function maList(extra: unknown[] = []) {
  return [
    { player_id: 'kitchen', name: 'Kitchen Speaker', state: 'playing', available: true, volume_level: 42 },
    { player_id: 'office', name: 'Office Speaker', state: 'idle', available: true, volume_level: 20 },
    { player_id: 'web-1', name: 'This Browser', state: 'playing', available: true },
    ...extra,
  ];
}

function renderSheet(value: SharedPlayer | null = player()) {
  const onClose = vi.fn();
  const result = render(
    <MemoryRouter>
      <MediaPlayerContext.Provider value={value}>
        <OutputSheet open onClose={onClose} />
      </MediaPlayerContext.Provider>
    </MemoryRouter>,
  );
  return { ...result, onClose };
}

describe('OutputSheet', () => {
  beforeEach(() => {
    vi.mocked(api.mediaStatus).mockResolvedValue(status() as never);
    vi.mocked(api.mediaTransport).mockResolvedValue({ status: 'SUCCESS' } as never);
    vi.mocked(sendMaRemoteCommand).mockImplementation(async (command: string) => {
      if (command === 'players/all') return maList() as never;
      return {} as never;
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('lists this device and deduplicates speakers Home Assistant and MA both know', async () => {
    renderSheet();

    expect(screen.getByRole('dialog', { name: 'Output' })).toBeTruthy();
    expect(await screen.findByText('Kitchen Speaker')).toBeTruthy();
    expect(screen.getAllByText('Kitchen Speaker')).toHaveLength(1);
    expect(screen.getAllByText('Office Speaker')).toHaveLength(1);
    expect(screen.getByText('Loft TV')).toBeTruthy();
    expect(screen.getByText('TVs')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'This device' })).toBeTruthy();
    expect(screen.getByText('Active')).toBeTruthy();
  });

  it('does not list the browser web player twice', async () => {
    renderSheet();
    await screen.findByText('Kitchen Speaker');
    expect(screen.queryByText('This Browser')).toBeNull();
  });

  it('moves the queue to a speaker with Play here and stops local playback', async () => {
    const value = player();
    const { onClose } = renderSheet(value);

    await screen.findByText('Office Speaker');
    await userEvent.click(screen.getByRole('button', { name: 'Play here on Office Speaker' }));

    expect(value.maCommand).toHaveBeenCalledWith('player_queues/transfer', {
      source_queue_id: 'web-1',
      target_queue_id: 'office',
    });
    expect(value.pause).toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('shows a busy speaker as controllable without moving the queue', async () => {
    const { onClose } = renderSheet();
    await screen.findByText('Kitchen Speaker');

    await userEvent.click(screen.getByRole('button', { name: 'Control Kitchen Speaker' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('sets a remote Music Assistant volume from its row', async () => {
    renderSheet();
    await screen.findByText('Kitchen Speaker');

    fireEvent.change(screen.getByRole('slider', { name: 'Volume for Kitchen Speaker' }), { target: { value: '35' } });

    await waitFor(() => expect(sendMaRemoteCommand).toHaveBeenCalledWith('players/cmd/volume_set', {
      player_id: 'kitchen',
      volume_level: 35,
    }));
  });

  it('reads the status silently so the poll never re-fires the connectivity toast', async () => {
    renderSheet();
    await screen.findByText('Kitchen Speaker');
    expect(api.mediaStatus).toHaveBeenCalledWith({ silent: true });
  });

  it('shows no volume slider for a speaker that reports none', async () => {
    vi.mocked(api.mediaStatus).mockResolvedValue(status([{ ...KITCHEN, volume_level: null }] as never) as never);
    vi.mocked(sendMaRemoteCommand).mockResolvedValue([] as never);
    renderSheet(player({ mediaTitle: null, isPlaying: false }));

    await screen.findByText('Kitchen Speaker');
    expect(screen.queryByRole('slider', { name: 'Volume for Kitchen Speaker' })).toBeNull();
  });

  it('reads a genuine 1% volume as 1, not 100', async () => {
    vi.mocked(api.mediaStatus).mockResolvedValue(status([{ ...KITCHEN, volume_level: 1 }] as never) as never);
    vi.mocked(sendMaRemoteCommand).mockResolvedValue([] as never);
    renderSheet(player({ mediaTitle: null, isPlaying: false }));

    await screen.findByText('Kitchen Speaker');
    const slider = screen.getByRole('slider', { name: 'Volume for Kitchen Speaker' }) as HTMLInputElement;
    expect(slider.value).toBe('1');
  });

  it('shows an unknown group volume rather than guessing a level', async () => {
    withMa({ 'web-1': { group_members: ['web-1', 'office'], can_group_with: ['office'] } });
    renderSheet();

    const panel = await screen.findByTestId('group-members');
    expect(within(panel).queryByRole('slider', { name: 'Group volume' })).toBeNull();
    expect(within(panel).getByText('--')).toBeTruthy();
  });

  it('sets a Home Assistant-only speaker volume through the media transport API', async () => {
    vi.mocked(api.mediaStatus).mockResolvedValue(status([{ ...TV, state: 'playing' }]) as never);
    vi.mocked(sendMaRemoteCommand).mockResolvedValue([] as never);
    renderSheet();

    await screen.findByText('Loft TV');
    const slider = screen.getByRole('slider', { name: 'Volume for Loft TV' });
    fireEvent.change(slider, { target: { value: '45' } });

    await waitFor(() => expect(api.mediaTransport).toHaveBeenCalledWith({
      entity_id: 'media_player.loft_tv',
      command: 'volume_set',
      volume_level: 0.45,
    }));
  });

  function withMa(overrides: Record<string, Record<string, unknown>>) {
    vi.mocked(sendMaRemoteCommand).mockImplementation(async (command: string) => {
      if (command === 'players/all') {
        return (maList() as Array<{ player_id: string }>)
          .map((entry) => ({ ...entry, ...(overrides[entry.player_id] ?? {}) })) as never;
      }
      return {} as never;
    });
  }

  it('offers grouping only with speakers Music Assistant can keep in sync', async () => {
    withMa({ 'web-1': { can_group_with: ['office'] } });
    renderSheet();
    await screen.findByText('Office Speaker');

    expect(screen.queryByRole('checkbox', { name: /Group Kitchen Speaker/ })).toBeNull();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Group Office Speaker with This device' }));

    await waitFor(() => expect(sendMaRemoteCommand).toHaveBeenCalledWith('players/cmd/set_members', {
      target_player: 'web-1',
      player_ids_to_add: ['office'],
      player_ids_to_remove: [],
    }));
    expect(screen.getByText(/Only speakers that can play in sync with This device/)).toBeTruthy();
  });

  it('removes a speaker from the group from the member list', async () => {
    withMa({ 'web-1': { group_members: ['web-1', 'office'], can_group_with: ['office'] } });
    renderSheet();

    const members = await screen.findByTestId('group-members');
    expect(members.textContent).toContain('Office Speaker');
    expect((screen.getByRole('checkbox', { name: 'Group Office Speaker with This device' }) as HTMLInputElement).checked).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Remove Office Speaker from the group' }));

    await waitFor(() => expect(sendMaRemoteCommand).toHaveBeenCalledWith('players/cmd/set_members', {
      target_player: 'web-1',
      player_ids_to_add: [],
      player_ids_to_remove: ['office'],
    }));
  });

  it('sets one volume for the whole group', async () => {
    withMa({ 'web-1': { group_members: ['web-1', 'office'], can_group_with: ['office'], group_volume: 40 } });
    renderSheet();
    const slider = await screen.findByRole('slider', { name: 'Group volume' });
    expect((slider as HTMLInputElement).value).toBe('40');
    fireEvent.change(slider, { target: { value: '65' } });
    await waitFor(() => expect(sendMaRemoteCommand).toHaveBeenCalledWith('players/cmd/group_volume', {
      player_id: 'web-1',
      volume_level: 65,
    }));
  });

  it('leads with a playing speaker when this device has nothing, and can end its group', async () => {
    vi.mocked(api.mediaStatus).mockResolvedValue(status([
      { ...KITCHEN, group_members: ['media_player.kitchen', 'media_player.office', 'media_player.den'] },
      OFFICE,
      TV,
    ]) as never);
    withMa({
      kitchen: { group_members: ['kitchen', 'office', 'den'], can_group_with: ['office', 'den'] },
    });
    renderSheet(player({ mediaTitle: null, isPlaying: false }));
    await screen.findByTestId('group-members');

    await userEvent.click(screen.getByRole('button', { name: 'Ungroup all' }));
    await waitFor(() => expect(sendMaRemoteCommand).toHaveBeenCalledWith('players/cmd/set_members', {
      target_player: 'kitchen',
      player_ids_to_add: [],
      player_ids_to_remove: ['office', 'den'],
    }));
  });

  it('says so when nothing can play in sync with the leader', async () => {
    renderSheet();
    await screen.findByText('Office Speaker');
    expect(screen.queryByRole('checkbox', { name: /Group/ })).toBeNull();
    expect(screen.getByText('No other speaker can play in sync with This device.')).toBeTruthy();
  });

  it('shows unavailable players dimmed and without actions', async () => {
    vi.mocked(sendMaRemoteCommand).mockImplementation(async (command: string) => {
      if (command === 'players/all') {
        return maList([{ player_id: 'attic', name: 'Attic Speaker', state: 'idle', available: false }]) as never;
      }
      return {} as never;
    });
    renderSheet();

    expect(await screen.findByText('Attic Speaker')).toBeTruthy();
    expect(screen.getAllByText('Offline').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Play here on Attic Speaker' })).toBeNull();
  });

  it('closes on Escape without closing the sheet above it', async () => {
    const { onClose } = renderSheet();
    await screen.findByText('Kitchen Speaker');

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
