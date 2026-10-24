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
export function proxiedImage(raw?: string | null, width?: number): string | null {
  if (!raw) return null;
  const mt = currentMediaToken();
  if (!mt) {
    void ensureMediaToken();
    return null;
  }
  const base = getServerOrigin().replace(/\/+$/, '');
  // `w` asks the upstream for an image this wide (MA's `size=`), so a 44 px
  // thumbnail does not download a 1500 px cover (BUG-11).
  const size = width && width > 0 ? `&w=${Math.round(width)}` : '';
  return `${base}/api/media/imageproxy?path=${encodeURIComponent(raw)}${size}&user=${encodeURIComponent(mt.user)}&mt=${encodeURIComponent(mt.token)}`;
}
