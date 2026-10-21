import { storageGetSync } from './storage';
import { getServerOrigin } from './serverUrl';

/**
 * Cover art through the gateway's per-user image proxy.
 *
 * Music Assistant hands out relative image paths and provider URLs; the
 * gateway is the only service that can fetch both with the right credentials.
 */
export function proxiedImage(raw?: string | null): string | null {
  if (!raw) return null;
  const apiToken = storageGetSync('jarvis_api_key') ?? '';
  const base = getServerOrigin().replace(/\/+$/, '');
  return `${base}/api/media/imageproxy?path=${encodeURIComponent(raw)}${apiToken ? `&token=${encodeURIComponent(apiToken)}` : ''}`;
}
