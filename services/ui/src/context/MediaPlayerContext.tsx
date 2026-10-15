import { createContext, useContext, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Capacitor } from '@capacitor/core';
import { useMAWebPlayer } from '../lib/maWebPlayer';
import type { MAWebPlayerState } from '../lib/maWebPlayer';
import { handleVisibilityChange, installPageHideListener } from '../lib/webPlayer';
import JarvisMedia from '../plugins/jarvisMedia';

type SharedPlayer = ReturnType<typeof useMAWebPlayer>;

const MediaPlayerContext = createContext<SharedPlayer | null>(null);

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

  useEffect(() => {
    installPageHideListener();
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  const native = Capacitor.isNativePlatform();
  const { mediaTitle, mediaArtist, isPlaying, isConnected } = player;
  useEffect(() => {
    if (!native) return;
    if (!isConnected && !isPlaying) {
      void JarvisMedia.stop().catch(() => {});
      return;
    }
    if (!mediaTitle && !isPlaying) return;
    void JarvisMedia.update({
      title: mediaTitle || 'Jarvis',
      artist: mediaArtist || '',
      playing: Boolean(isPlaying),
    }).catch(() => {});
  }, [native, mediaTitle, mediaArtist, isPlaying, isConnected]);

  useEffect(() => {
    if (!native) return;
    const handle = JarvisMedia.addListener('action', ({ action }) => {
      const p = playerRef.current;
      if (action === 'play') void p.play();
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
