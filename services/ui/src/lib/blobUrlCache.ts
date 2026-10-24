/**
 * One object URL per file for the life of the page. Files that need the
 * app's token (chat photos and voice messages, note attachments) cannot be
 * an <img src> straight from the API, so they are loaded once as a Blob and
 * shown through an object URL. Chats and notes re-render often; refetching
 * each time would flicker and waste data. A failed load is forgotten so the
 * next view tries again.
 */
const blobCache = new Map<string, Promise<string>>();

export function cachedUrl(key: string, load: () => Promise<Blob>): Promise<string> {
  let entry = blobCache.get(key);
  if (!entry) {
    entry = load().then((blob) => URL.createObjectURL(blob));
    entry.catch(() => blobCache.delete(key));
    blobCache.set(key, entry);
  }
  return entry;
}
