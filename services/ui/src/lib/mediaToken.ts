import { useEffect, useSyncExternalStore } from 'react';
import { storageGetSync } from './storage';
import { apiClient } from '../services/api';

/**
 * The signed media token (`?mt=&user=`, media overhaul §7.4) for URLs that
 * cannot send an Authorization header: cover art in `<img>` and the OS
 * MediaSession artwork.
 *
 * Kept in memory only, never in storage. It is minted by `POST
 * /api/media/token` with the signed-in API key, refreshed five minutes before
 * it expires, and dropped as soon as the stored API key changes (sign-out or
 * another account), so a URL is never signed for the wrong person.
 */
export interface MediaToken {
  token: string;
  user: string;
  /** Unix seconds. */
  expiresAt: number;
}

/** Refresh this many seconds before expiry (§7.4). */
export const REFRESH_LEAD_SECONDS = 300;

interface Held extends MediaToken {
  apiKey: string;
}

let held: Held | null = null;
let inflight: Promise<MediaToken | null> | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function apiKey(): string {
  return storageGetSync('jarvis_api_key') ?? '';
}

function notify(): void {
  listeners.forEach((listener) => listener());
}

function schedule(): void {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = null;
  if (!held) return;
  const delayMs = (held.expiresAt - REFRESH_LEAD_SECONDS) * 1000 - Date.now();
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    void refreshMediaToken();
  }, Math.max(0, delayMs));
}

/** The usable token, or null when none is held, it has expired, or the API key changed. */
export function currentMediaToken(): MediaToken | null {
  if (!held) return null;
  if (held.apiKey !== apiKey() || held.expiresAt * 1000 <= Date.now()) return null;
  return held;
}

/** Ask the gateway for a fresh token. Concurrent callers share one request. */
export function refreshMediaToken(): Promise<MediaToken | null> {
  if (inflight) return inflight;
  const key = apiKey();
  if (!key) return Promise.resolve(null);
  // The shared client adds the server URL and the bearer header.
  inflight = apiClient
    .post<{ token?: unknown; user?: unknown; expires_at?: unknown }>('/api/media/token')
    .then(({ data: body }) => {
      if (typeof body.token !== 'string' || typeof body.user !== 'string' || typeof body.expires_at !== 'number') {
        throw new Error('Unexpected media token response');
      }
      held = { token: body.token, user: body.user, expiresAt: body.expires_at, apiKey: key };
      schedule();
      notify();
      return currentMediaToken();
    })
    .catch(() => null)
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** The current token, fetching one first when none is usable or it is about to expire. */
export function ensureMediaToken(): Promise<MediaToken | null> {
  const token = currentMediaToken();
  if (token && token.expiresAt - REFRESH_LEAD_SECONDS > Date.now() / 1000) return Promise.resolve(token);
  return refreshMediaToken();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The current token for rendering; components re-render when it changes. */
export function useMediaToken(): MediaToken | null {
  const token = useSyncExternalStore(subscribe, currentMediaToken, currentMediaToken);
  useEffect(() => {
    void ensureMediaToken();
  }, []);
  return token;
}

/** Tests only: drop the held token and any pending refresh. */
export function resetMediaTokenForTests(): void {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = null;
  held = null;
  inflight = null;
  notify();
}
