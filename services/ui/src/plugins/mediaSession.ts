import { registerPlugin } from '@capacitor/core';

/* What other apps are playing right now (YouTube, Spotify, podcasts, browser
   media). Android only exposes this to an app granted Notification access, so
   this never prompts -- the person flips the toggle in Settings. */

export interface MediaSessionItem {
  app: string;
  title: string;
  artist: string;
  album: string;
  playing: boolean;
  durationMs: number;
  positionMs: number;
}

export interface MediaSessionAccess {
  granted: boolean;
}

export interface MediaSessionPluginInterface {
  hasAccess(): Promise<MediaSessionAccess>;
  openSettings(): Promise<void>;
  getActive(): Promise<{ sessions: MediaSessionItem[] }>;
  /** An APK whose notification listener reports plays itself (older APKs reject this). */
  nativeReports(): Promise<{ media: boolean }>;
}

/* On the web (and old APKs without the plugin) there is simply nothing to
   read -- never fabricate a session. */
const webFallback: MediaSessionPluginInterface = {
  hasAccess: async () => ({ granted: false }),
  openSettings: async () => undefined,
  getActive: async () => ({ sessions: [] }),
  nativeReports: async () => ({ media: false }),
};

const MediaSession = registerPlugin<MediaSessionPluginInterface>('MediaSession', {
  web: () => webFallback,
});

export default MediaSession;
