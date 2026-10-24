import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { useHaptics } from '../../../hooks/useHaptics';
import { errorText } from '../../../lib/errorText';
import type { PlayRequest, QueueOption } from '../../../components/media/types';
import { useMediaStore } from '../store/mediaStore';
import { outputKind, type OutputId, type RepeatMode } from '../types';
import { adapterFor, type WebPlayerHandle } from './adapters/adapters';
import { nextRepeat, type TransportCommand } from './adapters/PlayerAdapter';
import { refreshMediaStatus } from './statusFeed';

/** Volume drags send only the value they settle on. */
export const VOLUME_DEBOUNCE_MS = 150;

/**
 * Everything a media screen does to the selected output, routed through the
 * output's adapter: '' plays on this device's web player, `ma:<id>` on a
 * Music Assistant player through the gateway, a Home Assistant entity through
 * the execution service. Each screen (Listen Now, search, library, details,
 * the item menu) uses this one hook, so queueing on "the selected device"
 * means the same thing everywhere.
 *
 * Remote outputs update the store optimistically and roll back with a toast
 * when the device refuses (UX-53).
 */
export function usePlayback(web: WebPlayerHandle) {
  const { trigger } = useHaptics();
  const target = useMediaStore((s) => s.activeOutputId);
  const targetRef = useRef(target);
  const webRef = useRef(web);
  useEffect(() => {
    targetRef.current = target;
    webRef.current = web;
  }, [target, web]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const volumeTimer = useRef<number | null>(null);

  const adapter = useCallback((id: OutputId) => adapterFor(id, webRef.current), []);
  const store = () => useMediaStore.getState();

  /** Start `req` on the selected output; `key` names the row showing progress. */
  const play = useCallback(async (req: PlayRequest, key: string) => {
    void trigger('light');
    setBusy(key);
    setError(null);
    const id = targetRef.current;
    try {
      await adapter(id).play(id, req);
      if (outputKind(id) === 'ha') void refreshMediaStatus();
    } catch (err) {
      setError(errorText(err, 'Playback failed'));
    } finally {
      setBusy(null);
    }
  }, [adapter, trigger]);

  /** Add `req` to the selected output's queue without interrupting it. */
  const enqueue = useCallback(async (req: PlayRequest, option: QueueOption) => {
    void trigger('light');
    setError(null);
    const id = targetRef.current;
    try {
      await adapter(id).enqueue(id, req, option);
      toast.success(option === 'next' ? `${req.title} plays next` : `${req.title} added to the queue`);
    } catch (err) {
      setError(errorText(err, 'Could not add to the queue'));
    }
  }, [adapter, trigger]);

  const transport = useCallback(async (command: TransportCommand) => {
    void trigger('light');
    setBusy(command);
    setError(null);
    const id = targetRef.current;
    try {
      await adapter(id).transport(id, command);
      if (outputKind(id) === 'ma') {
        // Keep the card in step until the next poll; the poll stays the
        // authority, so a command MA accepted but could not act on self-corrects.
        const status = command === 'pause' ? 'paused' : command === 'stop' ? 'idle' : command === 'resume' ? 'playing' : null;
        if (status) store().actions.patchPlayer(id, { status });
      } else if (outputKind(id) === 'ha') {
        await refreshMediaStatus();
      }
    } catch (err) {
      setError(errorText(err, 'Command failed'));
    } finally {
      setBusy(null);
    }
  }, [adapter, trigger]);

  const seek = useCallback(async (seconds: number) => {
    const id = targetRef.current;
    if (id) store().actions.patchPlayer(id, { positionSec: seconds, positionUpdatedAt: Date.now() });
    try {
      await adapter(id).seek(id, seconds);
    } catch {
      // The optimistic position snaps back on the next update; say why.
      toast.error('Could not seek on that player');
    }
  }, [adapter]);

  const setVolume = useCallback((volume: number) => {
    const id = targetRef.current;
    if (!id) {
      void adapter(id).setVolume(id, volume);
      return;
    }
    store().actions.setVolume(id, volume);
    if (volumeTimer.current) clearTimeout(volumeTimer.current);
    volumeTimer.current = window.setTimeout(() => {
      volumeTimer.current = null;
      adapter(id).setVolume(id, volume).catch(() => toast.error('Could not change the volume'));
    }, VOLUME_DEBOUNCE_MS);
  }, [adapter]);

  const setMuted = useCallback((muted: boolean) => {
    const id = targetRef.current;
    if (id) store().actions.setMuted(id, muted);
    adapter(id).setMuted(id, muted).catch(() => {
      if (id) store().actions.setMuted(id, !muted);
      toast.error('Could not mute that player');
    });
  }, [adapter]);

  const setShuffle = useCallback(async (on: boolean) => {
    void trigger('light');
    const id = targetRef.current;
    const before = id ? store().players[id]?.shuffle ?? null : null;
    if (id) store().actions.patchPlayer(id, { shuffle: on });
    try {
      await adapter(id).setShuffle(id, on);
    } catch {
      if (id) store().actions.patchPlayer(id, { shuffle: before });
      toast.error('Could not change shuffle');
    }
  }, [adapter, trigger]);

  const setRepeat = useCallback(async (mode: RepeatMode) => {
    void trigger('light');
    const id = targetRef.current;
    const before = id ? store().players[id]?.repeat ?? null : null;
    if (id) store().actions.patchPlayer(id, { repeat: mode });
    try {
      await adapter(id).setRepeat(id, mode);
    } catch {
      if (id) store().actions.patchPlayer(id, { repeat: before });
      toast.error('Could not change repeat');
    }
  }, [adapter, trigger]);

  return useMemo(() => ({
    target, busy, error, clearError: () => setError(null),
    play, enqueue, transport, seek, setVolume, setMuted, setShuffle, setRepeat, nextRepeat,
  }), [target, busy, error, play, enqueue, transport, seek, setVolume, setMuted, setShuffle, setRepeat]);
}
