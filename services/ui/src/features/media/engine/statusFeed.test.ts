import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useMediaStatusFeed } from './statusFeed';
import { resetMediaStore, useMediaStore } from '../store/mediaStore';
import type { SharedPlayer } from '../../../context/webPlayerContext';

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
