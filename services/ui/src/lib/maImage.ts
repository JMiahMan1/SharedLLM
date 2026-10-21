/**
 * Extract a usable image reference from a Music Assistant image payload.
 *
 * MA sends several shapes and callers must handle all of them, otherwise the
 * UI silently falls back to a placeholder:
 *  - a full URL string (e.g. http://<ma-host>:8095/imageproxy/...)
 *  - a PlayerMedia `image_url` string (current_media on player_updated)
 *  - a QueueItem `image` string (current_item on queue_updated)
 *  - a MediaItemImage object: { type, path, provider, remote_url, ... }
 *  - an array of any of the above
 *  - a nested { image: <any> } wrapper
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
