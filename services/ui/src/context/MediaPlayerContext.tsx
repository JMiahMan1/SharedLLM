import { useCallback, useContext, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Capacitor } from '@capacitor/core';
import { clientName, useMAWebPlayer } from '../lib/maWebPlayer';
import type { MAWebPlayerState } from '../lib/maWebPlayer';
import JarvisMedia from '../plugins/jarvisMedia';
import { api } from '../services/api';
import { TabLock } from '../features/media/engine/tabLock';
import { applySessionMetadata, applySessionPosition, bindSessionHandlers } from '../features/media/engine/mediaSession';
import { useMediaToken } from '../lib/mediaToken';
import { useMediaStore } from '../features/media/store/mediaStore';

import { MediaPlayerContext } from './webPlayerContext';
import type { SharedPlayer } from './webPlayerContext';

/** How often a playing web player re-reports itself. The execution service
 *  treats a "playing" report older than two minutes as gone (app closed). */
const WEB_PLAYER_HEARTBEAT_MS = 15_000;

/** Audiobook/podcast URIs from Music Assistant, so the house sees what kind of
 *  thing is playing. */
function mediaKind(uri: string | null): 'audiobook' | 'podcast' | 'music' {
  if (uri?.includes('audiobook')) return 'audiobook';
  if (uri?.includes('podcast')) return 'podcast';
  return 'music';
}

/**
 * Tells the execution service what the web player is doing, so the dashboard
 * widget, the Android home-screen widget and voice ("what's playing?") see it.
 *
 * Lives with the player, not the Media page: playback outlives the page, and a
 * report that stopped when the page closed is what left "Web Player: playing"
 * on the dashboard long after the song ended.
 */
function useReportWebPlayerState(player: SharedPlayer) {
  const latest = useRef(player);
  useEffect(() => {
    latest.current = player;
  }, [player]);

  const report = useCallback((state: 'playing' | 'paused' | 'idle') => {
    const p = latest.current;
    void api.syncMediaState({
      entity_id: 'web_player',
      state,
      media_type: mediaKind(p.mediaUri),
      media_content_id: p.mediaUri || undefined,
      media_title: state === 'idle' ? undefined : p.mediaTitle || undefined,
      media_artist: state === 'idle' ? undefined : p.mediaArtist || undefined,
      position: p.position,
      duration: p.duration,
      volume_level: p.volume / 100,
      is_volume_muted: p.muted,
    }).catch(() => { /* best-effort: the next heartbeat retries */ });
  }, []);

  const { isPlaying, mediaTitle, mediaUri } = player;
  const reportedSomething = useRef(false);
  useEffect(() => {
    if (isPlaying) {
      reportedSomething.current = true;
      report('playing');
    } else if (mediaTitle) {
      // Only once this player has actually played: a fresh, idle app must
      // not overwrite what another device reported.
      if (reportedSomething.current) report('paused');
    } else if (reportedSomething.current) {
      reportedSomething.current = false;
      report('idle');
    }
  }, [isPlaying, mediaTitle, mediaUri, report]);

  useEffect(() => {
    if (!isPlaying) return;
    const id = window.setInterval(() => report('playing'), WEB_PLAYER_HEARTBEAT_MS);
    return () => clearInterval(id);
  }, [isPlaying, report]);
}

/**
 * One tab plays this device's audio at a time (BUG-66). Starting playback
 * claims it; when another tab claims it, this tab drops its audio connection
 * (pausing would pause the shared Music Assistant player for both) and the
 * mini player offers "Take over", which simply plays here again.
 */
function useTabLock(player: SharedPlayer) {
  const playerRef = useRef(player);
  useEffect(() => {
    playerRef.current = player;
  }, [player]);
  const lockRef = useRef<TabLock | null>(null);
  useEffect(() => {
    const lock = new TabLock();
    lockRef.current = lock;
    const { setPlayingInOtherTab } = useMediaStore.getState().actions;
    const offLost = lock.onLost(() => playerRef.current.disconnect());
    const offChange = lock.onChange((owner) => setPlayingInOtherTab(owner === 'other'));
    return () => {
      offLost();
      offChange();
      lock.dispose();
      lockRef.current = null;
    };
  }, []);
  const { isPlaying } = player;
  useEffect(() => {
    if (isPlaying) lockRef.current?.claim();
    else lockRef.current?.release();
  }, [isPlaying]);
}

/**
 * The lock screen, notification and media keys follow this device's playback
 * (§6.7). Metadata changes with the track; the scrubber with the position.
 */
function useOsMediaSession(player: SharedPlayer) {
  // Artwork URLs are signed with the media token; re-apply once it arrives.
  const token = useMediaToken();
  const { mediaTitle, mediaArtist, mediaImage, isPlaying, position, duration, playbackRate } = player;
  useEffect(() => {
    applySessionMetadata(mediaTitle
      ? { title: mediaTitle, artist: mediaArtist, image: mediaImage, isPlaying, position: 0, duration: 0, playbackRate: 1 }
      : null);
  }, [mediaTitle, mediaArtist, mediaImage, isPlaying, token]);
  useEffect(() => {
    applySessionPosition(mediaTitle
      ? { title: mediaTitle, artist: mediaArtist, image: mediaImage, isPlaying, position, duration, playbackRate }
      : null);
  }, [mediaTitle, mediaArtist, mediaImage, isPlaying, position, duration, playbackRate]);

  const playerRef = useRef(player);
  useEffect(() => {
    playerRef.current = player;
  }, [player]);
  useEffect(() => bindSessionHandlers({
    play: () => { void playerRef.current.play().catch(() => {}); },
    pause: () => { void playerRef.current.pause(); },
    stop: () => { void playerRef.current.stop(); },
    next: () => { void playerRef.current.next(); },
    previous: () => { void playerRef.current.previous(); },
    seekTo: (seconds) => { void playerRef.current.seek(seconds); },
    position: () => playerRef.current.position,
  }), []);
}

/**
 * The one web player for the whole app, mounted above the router.
 *
 * It used to belong to the Media page, whose unmount disconnected it -- so
 * opening any other tab stopped the music. Living here, it plays on while the
 * person moves around the app; on Android it also runs a media-playback
 * service so the OS does not freeze it in the background, with controls on the
 * lock screen and in the notification shade.
 */
export function MediaPlayerProvider({ children }: { children: ReactNode }) {
  const player = useMAWebPlayer();
  const playerRef = useRef(player);
  useEffect(() => {
    playerRef.current = player;
  }, [player]);

  useReportWebPlayerState(player);
  useTabLock(player);
  useOsMediaSession(player);

  const native = Capacitor.isNativePlatform();
  const { mediaTitle, mediaArtist, isPlaying, playerId, volume } = player;
  const serviceStarted = useRef(false);
  useEffect(() => {
    if (!native) return;
    if (!mediaTitle && !isPlaying) {
      // Nothing loaded (stopped, or never played): drop the notification.
      if (serviceStarted.current) {
        serviceStarted.current = false;
        void JarvisMedia.stop().catch(() => {});
      }
      return;
    }
    serviceStarted.current = true;
    void JarvisMedia.update({
      title: mediaTitle || 'Jarvis',
      artist: mediaArtist || '',
      playing: Boolean(isPlaying),
      playerId: playerId || undefined,
      playerName: clientName(),
      volume,
    }).catch(() => {});
  }, [native, mediaTitle, mediaArtist, isPlaying, playerId, volume]);

  // Opening the app again: if the phone kept playing natively after the app
  // was swiped away, take playback back and carry on here.
  useEffect(() => {
    if (!native) return;
    void JarvisMedia.reclaim()
      .then(({ playing }) => {
        if (playing) void playerRef.current.play().catch(() => {});
      })
      .catch(() => {});
  }, [native]);

  useEffect(() => {
    if (!native) return;
    const handle = JarvisMedia.addListener('action', ({ action }) => {
      const p = playerRef.current;
      if (action === 'play') void p.play().catch(() => {});
      else if (action === 'pause') void p.pause();
      else if (action === 'next') void p.next();
      else if (action === 'previous') void p.previous();
    });
    return () => {
      void handle.then((h) => h.remove()).catch(() => {});
    };
  }, [native]);

  return <MediaPlayerContext.Provider value={player}>{children}</MediaPlayerContext.Provider>;
}

/** The app's web player; `onStateChange` hears every change while mounted,
 *  starting with the current state (so a page reopened mid-song shows it). */
// eslint-disable-next-line react-refresh/only-export-components
export function useSharedMAWebPlayer(onStateChange?: (state: MAWebPlayerState) => void): SharedPlayer {
  const player = useContext(MediaPlayerContext);
  if (!player) throw new Error('useSharedMAWebPlayer needs a MediaPlayerProvider above it');
  const cbRef = useRef(onStateChange);
  useEffect(() => {
    cbRef.current = onStateChange;
  }, [onStateChange]);
  useEffect(() => {
    cbRef.current?.(player);
  }, [player]);
  return player;
}
