/**
 * Extract a usable image reference from a Music Assistant image payload.
 *
 * MA sends several shapes and callers must handle all of them, otherwise the
 * UI silently falls back to a placeholder:
 *  - a full URL string (e.g. http://<ma-host>:8095/imageproxy/...)
 *  - a PlayerMedia `image_url` string (current_media on player_updated)
 *  - a QueueItem `image` string (current_item on queue_updated)
 *  - a MediaItemImage object: { type, path, provider, proxy_id, remote_url, ... }
 *  - an array of any of the above
 *  - a nested { image: <any> } wrapper
 *
 * When MA supplies a `proxy_id` (every library image does), that id is the
 * only reliable handle: `path` values such as `album-716` are provider-
 * relative and the image proxy rejects them. The gateway serves
 * `/imageproxy/<proxy_id>` from the Music Assistant host.
 *
 * Return whatever the gateway's /api/media/imageproxy can resolve.
 */
export function extractMaImage(img: unknown): string | null {
  if (!img) return null;
  if (typeof img === 'string') return img.trim() || null;
  if (Array.isArray(img)) {
    for (const item of img) {
      const result = extractMaImage(item);
      if (result) return result;
    }
    return null;
  }
  if (typeof img === 'object') {
    const record = img as Record<string, unknown>;
    const proxyId = record['proxy_id'];
    if (typeof proxyId === 'string' && proxyId.trim()) {
      return `/imageproxy/${proxyId.trim()}`;
    }
    // `remote_url` carries the absolute upstream URL; `path`/`uri` are
    // relative and resolved by the gateway imageproxy against the MA host.
    for (const key of ['image_url', 'remote_url', 'uri', 'path', 'url']) {
      const value = record[key];
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
    if (record.image) return extractMaImage(record.image);
  }
  return null;
}

/**
 * An MA item's cover wherever Music Assistant put it: its own `image`, its
 * `metadata.images`, then its album's. Library tracks and albums come with
 * `image: null` and the cover in `metadata.images`, and a track's cover may
 * only be its album's, so reading `image` alone shows a placeholder.
 */
export function maItemImage(item: unknown): string | null {
  if (!item || typeof item !== 'object') return null;
  const record = item as Record<string, unknown>;
  const album = record.album && typeof record.album === 'object' ? (record.album as Record<string, unknown>) : null;
  for (const source of [record, album]) {
    if (!source) continue;
    const own = extractMaImage(source.image);
    if (own) return own;
    const metadata = source.metadata && typeof source.metadata === 'object' ? (source.metadata as Record<string, unknown>) : null;
    const listed = extractMaImage(metadata?.images);
    if (listed) return listed;
  }
  return null;
}
