import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import OutputSheet from './OutputSheet';
import { MediaPlayerContext } from '../../context/webPlayerContext';
import type { SharedPlayer } from '../../context/webPlayerContext';
import { sendMaRemoteCommand } from '../../lib/maRemotePlayer';

vi.mock('../../services/api', () => ({
  api: {
    mediaStatus: vi.fn(),
    mediaTransport: vi.fn().mockResolvedValue({ status: 'SUCCESS' }),
  },
}));

vi.mock('../../lib/maRemotePlayer', () => ({
  sendMaRemoteCommand: vi.fn().mockResolvedValue([]),
}));

import { api } from '../../services/api';

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

  it('groups another speaker with the playing speaker', async () => {
    renderSheet();
    await screen.findByText('Office Speaker');

    await userEvent.click(screen.getByRole('checkbox', { name: 'Group Office Speaker with Kitchen Speaker' }));

    await waitFor(() => expect(sendMaRemoteCommand).toHaveBeenCalledWith('players/cmd/group_many', {
      target_player: 'kitchen',
      child_player_ids: ['kitchen', 'office'],
    }));
  });

  it('removes a speaker from the group when its checkbox is cleared', async () => {
    vi.mocked(api.mediaStatus).mockResolvedValue(status([
      { ...KITCHEN, group_members: ['media_player.office'] },
      OFFICE,
      TV,
    ]) as never);
    renderSheet();
    await screen.findByText('Office Speaker');

    const checkbox = screen.getByRole('checkbox', { name: 'Group Office Speaker with Kitchen Speaker' });
    expect((checkbox as HTMLInputElement).checked).toBe(true);
    await userEvent.click(checkbox);

    await waitFor(() => expect(sendMaRemoteCommand).toHaveBeenCalledWith('players/cmd/ungroup', {
      player_id: 'office',
    }));
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
