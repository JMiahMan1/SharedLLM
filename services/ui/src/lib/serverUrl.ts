import { Capacitor } from '@capacitor/core';
import { storageGetSync } from './storage';

const SERVER_URL_KEY = 'jarvis_server_url';

/**
 * Resolves the origin the app should talk to for API/WebSocket calls.
 *
 * On the web this is simply the current page origin (same-origin to the
 * gateway). Inside the Capacitor app the page is bundled and served from
 * `localhost`, so `window.location.origin` is useless for reaching the
 * backend — we must use the server URL the user configured in the app
 * (`jarvis_server_url`, set via the server-config banner / login).
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
  }
  return window.location.origin;
}

export function getWsProtocolFor(origin: string): 'wss' | 'ws' {
  return origin.startsWith('https') ? 'wss' : 'ws';
}
