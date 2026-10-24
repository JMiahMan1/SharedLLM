import { createContext, useCallback, useContext, useEffect, useMemo } from 'react';
import { Outlet, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { api } from '../../../services/api';
import { useHaptics } from '../../../hooks/useHaptics';
import { useSharedMAWebPlayer } from '../../../context/MediaPlayerContext';
import type { SharedPlayer } from '../../../context/webPlayerContext';
import type { DeviceOption } from '../../../components/media/types';
import { mediaKeys } from '../api/keys';
import { useMediaStatusFeed } from '../engine/statusFeed';
import { usePlayback } from '../engine/usePlayback';
import { useMediaActions, useMediaStore } from '../store/mediaStore';
import { deviceOptions, selectPlayers } from '../store/selectors';
import type { OutputId } from '../types';

/** What every media route shares: this device's player, the playback
 *  routing for the selected output, and the device list. */
export interface MediaScreenValue {
  player: SharedPlayer;
  playback: ReturnType<typeof usePlayback>;
  devices: DeviceOption[];
  selectDevice: (id: OutputId) => void;
}

const MediaScreenContext = createContext<MediaScreenValue | null>(null);

// eslint-disable-next-line react-refresh/only-export-components
export function useMediaScreen(): MediaScreenValue {
  const value = useContext(MediaScreenContext);
  if (!value) throw new Error('useMediaScreen needs the media routes around it');
  return value;
}

/**
 * The frame around every media route: keeps the remote players current,
 * applies `?output=` (the output picker's "Control" deep link), and shows a
 * failed action once, above whichever screen is open.
 */
export function MediaScreen() {
  const { trigger } = useHaptics();
  const player = useSharedMAWebPlayer();
  const playback = usePlayback(player);
  const players = useMediaStore(selectPlayers);
  const { selectOutput } = useMediaActions();
  useMediaStatusFeed(player);

  const [searchParams] = useSearchParams();
  const requestedOutput = searchParams.get('output');
  useEffect(() => {
    if (requestedOutput) selectOutput(requestedOutput);
  }, [requestedOutput, selectOutput]);

  const { data: entities = [] } = useQuery({
    queryKey: mediaKeys.entities(),
    queryFn: () => api.getEntities(),
    select: (data) => data.filter((e) => e.domain === 'media_player'),
    staleTime: 60_000,
  });
  const devices = useMemo(() => deviceOptions(entities, players, player.playerId), [entities, players, player.playerId]);

  const { clearError } = playback;
  const selectDevice = useCallback((id: OutputId) => {
    void trigger('medium');
    selectOutput(id);
    clearError();
    // Remember the choice server-side (voice "pause" and the dashboard follow
    // it). The web player records itself once it plays.
    if (id && !id.startsWith('ma:')) {
      void api.syncMediaState({ entity_id: id, state: 'idle' }).catch(() => {});
    }
  }, [selectOutput, clearError, trigger]);

  const value = useMemo(() => ({ player, playback, devices, selectDevice }), [player, playback, devices, selectDevice]);

  return (
    <MediaScreenContext.Provider value={value}>
      <div className="mx-auto max-w-6xl space-y-6 pb-clear-bottom-bar">
        {playback.error ? (
          <div role="alert" className="glass-panel flex items-center justify-between gap-3 rounded-[var(--radius-card)] p-3 text-sm text-[var(--site-text)]">
            <span className="min-w-0 break-words">{playback.error}</span>
            <button type="button" onClick={clearError} aria-label="Dismiss" className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-xl hover:bg-[var(--site-card-bg-hover)]">
              <X size={16} aria-hidden />
            </button>
          </div>
        ) : null}
        <Outlet />
      </div>
    </MediaScreenContext.Provider>
  );
}
