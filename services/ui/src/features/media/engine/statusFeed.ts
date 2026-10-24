import { useCallback, useEffect } from 'react';
import { api } from '../../../services/api';
import { listRemoteMaPlayers } from '../../../lib/maRemotePlayer';
import type { MediaStatus } from '../../../components/media/types';
import type { SharedPlayer } from '../../../context/webPlayerContext';
import { useMediaStore } from '../store/mediaStore';

export const STATUS_POLL_MS = 3000;

/** Fetch `/execute/media/status` once and fold it into the store. */
export async function refreshMediaStatus(): Promise<void> {
  try {
    const resp = await api.mediaStatus();
    if (resp.status !== 'SUCCESS' || !resp.detail) return;
    const detail = resp.detail as { active?: MediaStatus | null; all_players?: MediaStatus[] };
    useMediaStore.getState().actions.ingestStatus(detail.all_players ?? [], detail.active ?? null);
  } catch {
    /* the next poll retries */
  }
}

/**
 * Keep the store's remote players current while a media screen is open:
 * Home Assistant's players every 3 s (skipped while the tab is hidden) and
 * Music Assistant's own player list. The list comes over the web player's
 * socket when it is already connected, else through the gateway, so looking
 * at a speaker never makes this browser a player. A selected Music Assistant
 * speaker's state only comes from that list, so it is then refreshed too.
 */
export function useMediaStatusFeed(
  web: Pick<SharedPlayer, 'isConnected' | 'listMaPlayers' | 'playerId'> | null,
): { refresh: () => Promise<void> } {
  const webConnected = web?.isConnected ?? false;
  const listOverSocket = web?.listMaPlayers;
  const webPlayerId = web?.playerId ?? null;
  const maTarget = useMediaStore((s) => s.activeOutputId.startsWith('ma:'));

  useEffect(() => {
    void refreshMediaStatus();
    const id = window.setInterval(() => {
      if (!document.hidden) void refreshMediaStatus();
    }, STATUS_POLL_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const players = webConnected && listOverSocket ? await listOverSocket() : await listRemoteMaPlayers();
      if (!cancelled) useMediaStore.getState().actions.ingest({ type: 'ma-players', players, webPlayerId });
    };
    void load();
    const id = maTarget ? window.setInterval(() => { if (!document.hidden) void load(); }, STATUS_POLL_MS * 2) : null;
    return () => {
      cancelled = true;
      if (id !== null) clearInterval(id);
    };
  }, [webConnected, listOverSocket, webPlayerId, maTarget]);

  return { refresh: useCallback(() => refreshMediaStatus(), []) };
}
