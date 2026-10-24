import { getServerOrigin } from './serverUrl';
import { currentMediaToken, ensureMediaToken } from './mediaToken';

/**
 * Cover art through the gateway's per-user image proxy.
 *
 * Music Assistant hands out relative image paths and provider URLs; the
 * gateway is the only service that can fetch both with the right credentials.
 * An `<img>` cannot send headers, so the URL carries the signed media token
 * (`?user=&mt=`, §7.4), never the API key. Until a token is held this returns
 * null (the caller shows its placeholder) and asks for one; components that
 * call it subscribe with `useMediaToken()` so they re-render once it arrives.
 */
export function proxiedImage(raw?: string | null): string | null {
  if (!raw) return null;
  const mt = currentMediaToken();
  if (!mt) {
    void ensureMediaToken();
    return null;
  }
  const base = getServerOrigin().replace(/\/+$/, '');
  return `${base}/api/media/imageproxy?path=${encodeURIComponent(raw)}&user=${encodeURIComponent(mt.user)}&mt=${encodeURIComponent(mt.token)}`;
}
