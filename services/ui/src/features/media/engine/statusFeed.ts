import { useCallback, useEffect } from 'react';
import { api } from '../../../services/api';
import { listRemoteMaPlayers } from '../../../lib/maRemotePlayer';
import type { MediaStatus } from '../types';
import type { SharedPlayer } from '../../../context/webPlayerContext';
import { useMediaStore } from '../store/mediaStore';
import { MediaEventStream } from './eventStream';

/** How often the selected Music Assistant speaker is re-read while the stream is down. */
export const MA_LIST_POLL_MS = 6000;

/**
 * Is a home hub connected? `/execute/media/status` lists the hub's players, so
 * with no hub there is nothing to read there (the call only fails) and Music
 * Assistant's players, from its own list and the event stream, are all there
 * is. `null`: the integrations status is not in yet, so the read waits for it.
 */
let hubPlayers: boolean | null = true;

/** Fetch `/execute/media/status` once and fold it into the store. */
export async function refreshMediaStatus(): Promise<void> {
  if (hubPlayers === null) return;
  if (!hubPlayers) {
    // No hub: a read with no hub players in it, so a screen waiting for the
    // first read stops loading instead of reporting a failure.
    useMediaStore.getState().actions.ingestStatus([], null);
    useMediaStore.getState().actions.setStatusReadError(false);
    return;
  }
  try {
    const resp = await api.mediaStatus({ silent: true });
    if (resp.status !== 'SUCCESS' || !resp.detail) {
      useMediaStore.getState().actions.setStatusReadError(true);
      return;
    }
    const detail = resp.detail as { active?: MediaStatus | null; all_players?: MediaStatus[] };
    useMediaStore.getState().actions.ingestStatus(detail.all_players ?? [], detail.active ?? null);
    useMediaStore.getState().actions.setStatusReadError(false);
  } catch {
    // The next poll (or the event stream) catches up; a view can stop loading.
    useMediaStore.getState().actions.setStatusReadError(true);
  }
}

let users = 0;
let stream: MediaEventStream | null = null;

/** Say whether a home hub is connected; a change re-reads the status for the screens open now. */
export function setMediaHubPlayers(on: boolean | null): void {
  if (on === hubPlayers) return;
  hubPlayers = on;
  if (users > 0) void refreshMediaStatus();
}

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
 *
 * `hub` says whether a home hub is connected (`null` while that is not known
 * yet); without one the hub's players are not asked for.
 */
export function useMediaStatusFeed(
  web: Pick<SharedPlayer, 'isConnected' | 'listMaPlayers' | 'playerId'> | null,
  opts: { maPlayers?: boolean; hub?: boolean | null } = {},
): { refresh: () => Promise<void> } {
  const withMaPlayers = opts.maPlayers ?? true;
  const hub = opts.hub === undefined ? true : opts.hub;
  const webConnected = web?.isConnected ?? false;
  const listOverSocket = web?.listMaPlayers;
  const webPlayerId = web?.playerId ?? null;
  const maTarget = useMediaStore((s) => s.activeOutputId.startsWith('ma:'));
  const streamOpen = useMediaStore((s) => s.events === 'open');

  // Before the feed starts, so its first read already knows whether to ask the hub.
  useEffect(() => setMediaHubPlayers(hub), [hub]);
  useEffect(() => acquireMediaFeed(), []);

  useEffect(() => {
    // A screen that only shows what is playing (the dashboard card) does not
    // need Music Assistant's list of every speaker.
    if (!withMaPlayers) return;
    let cancelled = false;
    const load = async () => {
      const players = webConnected && listOverSocket ? await listOverSocket() : await listRemoteMaPlayers();
      // A failed read is unknown, not an empty list: keep what we already show
      // rather than wiping the output picker on a transient MA error.
      if (!cancelled && players) useMediaStore.getState().actions.ingest({ type: 'ma-players', players, webPlayerId });
    };
    void load();
    const id = maTarget && !streamOpen
      ? window.setInterval(() => { if (!document.hidden) void load(); }, MA_LIST_POLL_MS)
      : null;
    return () => {
      cancelled = true;
      if (id !== null) clearInterval(id);
    };
  }, [withMaPlayers, webConnected, listOverSocket, webPlayerId, maTarget, streamOpen]);

  return { refresh: useCallback(() => refreshMediaStatus(), []) };
}
