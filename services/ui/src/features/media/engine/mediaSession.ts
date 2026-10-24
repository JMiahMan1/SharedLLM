import { proxiedImage } from '../../../lib/mediaImage';

/**
 * The OS media session (lock screen, notification, hardware media keys) for
 * this device's playback (§6.7, BUG-56). Kept apart from React so the rules
 * are testable: artwork always goes through the image proxy, the position is
 * reported for the system scrubber, and everything is cleared when nothing is
 * loaded.
 */

export const ARTWORK_SIZES = [96, 192, 256, 512] as const;
export const SEEK_BACK_SECONDS = 15;
export const SEEK_FORWARD_SECONDS = 30;

export interface SessionState {
  title: string | null;
  artist: string | null;
  album?: string | null;
  image: string | null;
  isPlaying: boolean;
  position: number;
  duration: number;
  playbackRate: number;
}

export interface SessionHandlers {
  play: () => void;
  pause: () => void;
  stop: () => void;
  next: () => void;
  previous: () => void;
  seekTo: (seconds: number) => void;
  /** The current position, read when a skip action arrives. */
  position: () => number;
}

type SessionLike = Pick<MediaSession, 'metadata' | 'playbackState' | 'setActionHandler'> & {
  setPositionState?: MediaSession['setPositionState'];
};

function defaultSession(): SessionLike | null {
  return typeof navigator !== 'undefined' && 'mediaSession' in navigator ? navigator.mediaSession : null;
}

/** Proxied artwork at each size the OS may want; none until the media token is held. */
export function sessionArtwork(image: string | null | undefined): MediaImage[] {
  const out: MediaImage[] = [];
  for (const size of ARTWORK_SIZES) {
    const src = proxiedImage(image, size);
    if (src) out.push({ src, sizes: `${size}x${size}`, type: 'image/jpeg' });
  }
  return out;
}

/** Title, artist and artwork; null (nothing loaded) clears the session. */
export function applySessionMetadata(state: SessionState | null, session: SessionLike | null = defaultSession()): void {
  if (!session) return;
  try {
    if (!state || !state.title) {
      session.metadata = null;
      session.playbackState = 'none';
      return;
    }
    session.metadata = new MediaMetadata({
      title: state.title,
      artist: state.artist ?? '',
      album: state.album ?? '',
      artwork: sessionArtwork(state.image),
    });
    session.playbackState = state.isPlaying ? 'playing' : 'paused';
  } catch {
    /* MediaSession unsupported in this WebView */
  }
}

/** The system scrubber; live streams (no length) report none. */
export function applySessionPosition(state: SessionState | null, session: SessionLike | null = defaultSession()): void {
  if (!session?.setPositionState || !state?.title || !(state.duration > 0)) return;
  try {
    session.setPositionState({
      duration: state.duration,
      position: Math.min(Math.max(0, state.position), state.duration),
      playbackRate: state.playbackRate > 0 ? state.playbackRate : 1,
    });
  } catch {
    /* an out-of-range report is dropped, not fatal */
  }
}

/** Both at once. */
export function applySessionState(state: SessionState | null, session: SessionLike | null = defaultSession()): void {
  applySessionMetadata(state, session);
  applySessionPosition(state, session);
}

/** Route the OS buttons to `handlers`; returns the unbinding. */
export function bindSessionHandlers(handlers: SessionHandlers, session: SessionLike | null = defaultSession()): () => void {
  if (!session) return () => {};
  const actions: Array<[MediaSessionAction, MediaSessionActionHandler]> = [
    ['play', () => handlers.play()],
    ['pause', () => handlers.pause()],
    ['stop', () => handlers.stop()],
    ['nexttrack', () => handlers.next()],
    ['previoustrack', () => handlers.previous()],
    ['seekto', (details) => { if (typeof details.seekTime === 'number') handlers.seekTo(details.seekTime); }],
    ['seekbackward', (details) => handlers.seekTo(Math.max(0, handlers.position() - (details.seekOffset ?? SEEK_BACK_SECONDS)))],
    ['seekforward', (details) => handlers.seekTo(handlers.position() + (details.seekOffset ?? SEEK_FORWARD_SECONDS))],
  ];
  const set = (action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
    try {
      session.setActionHandler(action, handler);
    } catch {
      /* this browser does not support the action */
    }
  };
  for (const [action, handler] of actions) set(action, handler);
  return () => {
    for (const [action] of actions) set(action, null);
  };
}
