import { Capacitor } from '@capacitor/core';
import { storageGetSync } from './storage';
import { DEFAULT_SERVER_URL } from './serverDefaults';

export { DEFAULT_SERVER_URL, SERVER_URL_EXAMPLE } from './serverDefaults';
const SERVER_URL_KEY = 'jarvis_server_url';

/**
 * Resolves the origin the app should talk to for API/WebSocket calls.
 *
 * On the web this is simply the current page origin (same-origin to the
 * gateway). Inside the Capacitor app the page is bundled and served from
 * `localhost`, so `window.location.origin` is useless for reaching the
 * backend. We must use the server URL the user configured in the app
 * (`jarvis_server_url`, set via the server-config banner / login),
 * defaulting to the build's VITE_DEFAULT_SERVER_URL. With neither it throws
 * a descriptive error rather than guess; the server-config banner shows
 * "Server not configured" for the same state.
 */
export function getServerOrigin(): string {
  if (Capacitor.isNativePlatform()) {
    const serverUrl = storageGetSync(SERVER_URL_KEY);
    if (serverUrl) {
      try {
        return new URL(serverUrl).origin;
      } catch {
        return serverUrl;
      }
    }
    if (DEFAULT_SERVER_URL) return DEFAULT_SERVER_URL;
    // Never guess: the bundled page's own origin is not the server.
    throw new Error(
      'No Jarvis server is configured. Enter the server URL on the sign-in screen or in the '
        + '"Server not configured" banner, or build the app with VITE_DEFAULT_SERVER_URL set '
        + '(for example VITE_DEFAULT_SERVER_URL=https://jarvis.example.com npm run build).',
    );
  }
  return window.location.origin;
}

export function getWsProtocolFor(origin: string): 'wss' | 'ws' {
  return origin.startsWith('https') ? 'wss' : 'ws';
}
