import { useEffect } from 'react';

import { storageGetSync } from '../../lib/storage';
import MediaSession from '../../plugins/mediaSession';

const MEDIA_SESSION_SYNC_INTERVAL_MS = 5 * 60 * 1000;

let mediaSessionSyncInFlight = false;
// Whether the APK reports plays itself, asked once per app start: null until
// asked. Its notification listener runs while Jarvis is closed too, so the
// WebView then stays out of it and nothing is reported twice.
let nativeMedia: boolean | null = null;
// Keys we have already reported, so a song playing for an hour does not
// land on the feed fifty times. A key leaves the set when it stops, so the
// next time it plays it is reported again.
const reportedKeys = new Set<string>();

function sessionKey(session: { app: string; title: string }): string {
  return `${session.app}|${session.title}`;
}

async function syncMediaSessions(): Promise<void> {
  if (mediaSessionSyncInFlight) return;
  mediaSessionSyncInFlight = true;
  try {
    if (nativeMedia === null) {
      // An older APK rejects the call (or has no such method at all).
      nativeMedia = await Promise.resolve()
        .then(() => MediaSession.nativeReports())
        .then((r) => Boolean(r?.media))
        .catch(() => false);
    }
    if (nativeMedia) return;
    const access = await MediaSession.hasAccess();
    if (!access?.granted) return;
    const token = storageGetSync('jarvis_api_key');
    if (!token) return;
    const serverUrl = storageGetSync('jarvis_server_url') || window.location.origin;
    const { sessions } = await MediaSession.getActive();
    if (!Array.isArray(sessions)) return;

    const activeKeys = new Set<string>();
    for (const session of sessions) {
      if (!session || !session.playing) continue;
      const key = sessionKey(session);
      activeKeys.add(key);
      if (reportedKeys.has(key)) continue;
      try {
        await fetch(`${serverUrl}/api/activity`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            feature: 'media',
            event: 'play',
            item: session.title,
            detail: {
              app: session.app,
              artist: session.artist,
              album: session.album,
              playing: true,
              source: 'external',
            },
          }),
        });
        reportedKeys.add(key);
      } catch {
        // Quiet by design; the next tick retries.
      }
    }
    // Drop keys that are no longer active so a replay is reported.
    for (const key of Array.from(reportedKeys)) {
      if (!activeKeys.has(key)) reportedKeys.delete(key);
    }
  } catch {
    // Quiet by design.
  } finally {
    mediaSessionSyncInFlight = false;
  }
}

/**
 * Reports what other apps are playing (YouTube, Spotify, podcasts, browser
 * media) so the family profile sees listening outside Jarvis, on an APK that
 * does not report plays itself. Android-only; does nothing where the plugin
 * is missing.
 */
export default function MediaSessionSync() {
  useEffect(() => {
    void syncMediaSessions();
    const timer = window.setInterval(() => {
      void syncMediaSessions();
    }, MEDIA_SESSION_SYNC_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  return null;
}
