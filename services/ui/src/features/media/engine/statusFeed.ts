import { useCallback, useEffect } from 'react';
import { api } from '../../../services/api';
import { listRemoteMaPlayers } from '../../../lib/maRemotePlayer';
import type { MediaStatus } from '../types';
import type { SharedPlayer } from '../../../context/webPlayerContext';
import { useMediaStore } from '../store/mediaStore';
import { MediaEventStream } from './eventStream';

/** How often the selected Music Assistant speaker is re-read while the stream is down. */
export const MA_LIST_POLL_MS = 6000;

/** Fetch `/execute/media/status` once and fold it into the store. */
export async function refreshMediaStatus(): Promise<void> {
  try {
    const resp = await api.mediaStatus({ silent: true });
    if (resp.status !== 'SUCCESS' || !resp.detail) return;
    const detail = resp.detail as { active?: MediaStatus | null; all_players?: MediaStatus[] };
    useMediaStore.getState().actions.ingestStatus(detail.all_players ?? [], detail.active ?? null);
  } catch {
    /* the next poll (or the event stream) catches up */
  }
}

let users = 0;
let stream: MediaEventStream | null = null;

/** Start the shared feed for one more screen; returns its release. */
export function acquireMediaFeed(): () => void {
  users += 1;
  if (users === 1) {
    // Names, TV/speaker kinds and Music Assistant ids only come with the full
    // status, so read it once; the stream keeps state current after that.
    void refreshMediaStatus();
    const { actions } = useMediaStore.getState();
    stream = new MediaEventStream({
      onEvent: (event) => useMediaStore.getState().actions.ingest(event),
      onState: (state) => actions.setEvents(state),
      poll: refreshMediaStatus,
    });
    stream.start();
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    users -= 1;
    if (users === 0) {
      stream?.stop();
      stream = null;
    }
  };
}

/**
 * Keep the store's remote players current while a media screen is open: one
 * status read, then the server's event stream (polling only while it is
 * down), shared by every screen that asks.
 *
 * Music Assistant's player list (names of MA-only speakers) is read once. It
 * comes over the web player's socket when that is already connected, else
 * through the gateway, so looking at a speaker never makes this browser a
 * player. A selected MA speaker is re-read every 6 s only while the stream is
 * not delivering its updates.
 */
export function useMediaStatusFeed(
  web: Pick<SharedPlayer, 'isConnected' | 'listMaPlayers' | 'playerId'> | null,
): { refresh: () => Promise<void> } {
  const webConnected = web?.isConnected ?? false;
  const listOverSocket = web?.listMaPlayers;
  const webPlayerId = web?.playerId ?? null;
  const maTarget = useMediaStore((s) => s.activeOutputId.startsWith('ma:'));
  const streamOpen = useMediaStore((s) => s.events === 'open');

  useEffect(() => acquireMediaFeed(), []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const players = webConnected && listOverSocket ? await listOverSocket() : await listRemoteMaPlayers();
      if (!cancelled) useMediaStore.getState().actions.ingest({ type: 'ma-players', players, webPlayerId });
    };
    void load();
    const id = maTarget && !streamOpen
      ? window.setInterval(() => { if (!document.hidden) void load(); }, MA_LIST_POLL_MS)
      : null;
    return () => {
      cancelled = true;
      if (id !== null) clearInterval(id);
    };
  }, [webConnected, listOverSocket, webPlayerId, maTarget, streamOpen]);

  return { refresh: useCallback(() => refreshMediaStatus(), []) };
}
