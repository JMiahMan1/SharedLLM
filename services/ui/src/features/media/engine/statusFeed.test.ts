import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useMediaStatusFeed } from './statusFeed';
import { resetMediaStore, useMediaStore } from '../store/mediaStore';
import type { SharedPlayer } from '../../../context/webPlayerContext';
import { api } from '../../../services/api';

vi.mock('../../../services/api', () => ({
  api: { mediaStatus: vi.fn().mockResolvedValue({ status: 'FAILURE' }) },
}));

const seedOffice = () => useMediaStore.getState().actions.ingest({
  type: 'ma-players',
  webPlayerId: 'me',
  players: [{ player_id: 'office', name: 'Office', available: true, state: 'playing', powered: true }],
});

function web(listMaPlayers: SharedPlayer['listMaPlayers']): SharedPlayer {
  return { isConnected: true, listMaPlayers, playerId: 'me' } as unknown as SharedPlayer;
}

/**
 * A transient Music Assistant `players/all` failure must not wipe the output
 * picker: a failed read is unknown, so the previous list stays.
 */
describe('useMediaStatusFeed Music Assistant list', () => {
  beforeEach(() => resetMediaStore());

  it('keeps the previous MA list when the read fails', async () => {
    seedOffice();
    const listMaPlayers = vi.fn().mockResolvedValue(null);
    const { unmount } = renderHook(() => useMediaStatusFeed(web(listMaPlayers)));
    await waitFor(() => expect(listMaPlayers).toHaveBeenCalled());
    expect(useMediaStore.getState().players['ma:office']).toBeDefined();
    unmount();
  });

  it('replaces the MA list when the read succeeds', async () => {
    seedOffice();
    const listMaPlayers = vi.fn().mockResolvedValue([
      { player_id: 'den', name: 'Den', available: true, state: 'idle', powered: true },
    ]);
    const { unmount } = renderHook(() => useMediaStatusFeed(web(listMaPlayers)));
    await waitFor(() => expect(useMediaStore.getState().players['ma:den']).toBeDefined());
    expect(useMediaStore.getState().players['ma:office']).toBeUndefined();
    unmount();
  });
});

/**
 * `/execute/media/status` lists the home hub's players. With no hub it is not
 * asked at all, and the first read still completes (with no hub players), so
 * a screen waiting on it shows Music Assistant's speakers instead of an error.
 */
describe('useMediaStatusFeed and the home hub', () => {
  beforeEach(() => {
    resetMediaStore();
    vi.mocked(api.mediaStatus).mockClear();
  });

  it('does not ask for the hub players without a hub, and counts the read as done', async () => {
    seedOffice();
    const listMaPlayers = vi.fn().mockResolvedValue(null);
    const { unmount } = renderHook(() => useMediaStatusFeed(web(listMaPlayers), { hub: false }));
    await waitFor(() => expect(useMediaStore.getState().statusReadAt).not.toBeNull());
    expect(useMediaStore.getState().statusReadError).toBe(false);
    expect(useMediaStore.getState().players['ma:office']).toBeDefined();
    expect(api.mediaStatus).not.toHaveBeenCalled();
    unmount();
  });

  it('waits while the integrations status is unknown, then reads once a hub is confirmed', async () => {
    const listMaPlayers = vi.fn().mockResolvedValue(null);
    const { rerender, unmount } = renderHook(
      ({ hub }: { hub: boolean | null }) => useMediaStatusFeed(web(listMaPlayers), { hub }),
      { initialProps: { hub: null as boolean | null } },
    );
    await waitFor(() => expect(listMaPlayers).toHaveBeenCalled());
    expect(api.mediaStatus).not.toHaveBeenCalled();
    rerender({ hub: true });
    await waitFor(() => expect(api.mediaStatus).toHaveBeenCalled());
    unmount();
  });
});
