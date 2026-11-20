import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { api } from '../../services/api';
import type { PrivateVault } from '../../lib/privateChatCrypto';

/** Stop the browser offering to save, share, drag or open a picture elsewhere. */
export const noSave = {
  draggable: false,
  onContextMenu: (event: { preventDefault: () => void }) => event.preventDefault(),
  onDragStart: (event: { preventDefault: () => void }) => event.preventDefault(),
};

interface Shared {
  epoch: number;
  refs: number;
  promise: Promise<string>;
  url?: string;
  /** Released for good: its URL is (or will be, once made) revoked. */
  gone: boolean;
}

/**
 * Decrypted pictures in use right now, per vault, by chat and blob: the tile,
 * the viewer and the slideshow showing the same picture share one decrypt and
 * one object URL. In memory only, counted, and let go (revoked) the moment the
 * last screen using it closes; the vault revokes everything on lock anyway.
 */
const shared = new WeakMap<PrivateVault, Map<string, Shared>>();

function acquirePicture(vault: PrivateVault, token: string, blob: string, mime: string): Shared {
  let byKey = shared.get(vault);
  if (!byKey) {
    byKey = new Map();
    shared.set(vault, byKey);
  }
  const key = `${token}\n${blob}`;
  const held = byKey.get(key);
  // A release (lock, chat off the screen) revoked every URL: anything from before it is dead.
  if (held && held.epoch === vault.epoch && !held.gone) {
    held.refs += 1;
    return held;
  }
  const entry: Shared = { epoch: vault.epoch, refs: 1, promise: Promise.resolve(''), gone: false };
  entry.promise = api
    .getPrivateBlob(token, blob)
    .then((bytes) => vault.pictureUrl(bytes, mime))
    .then((url) => {
      entry.url = url;
      // Everyone stopped wanting it while it was decrypting.
      if (entry.gone) vault.revoke(url);
      return url;
    });
  entry.promise.catch(() => {
    if (byKey.get(key) === entry) byKey.delete(key);
  });
  byKey.set(key, entry);
  return entry;
}

/**
 * How long an unused picture is kept before its URL is revoked: long enough
 * for a screen that remounts (or a tile handing over to the viewer) to pick it
 * up again without a second download and decrypt, short enough that a closed
 * gallery or slideshow is not holding pictures in memory.
 */
const GRACE_MS = 1500;

/** Let go of one use of a picture: the very entry acquired, never a newer one under the same key. */
function releasePicture(vault: PrivateVault, token: string, blob: string, held: Shared): void {
  held.refs -= 1;
  if (held.refs > 0) return;
  window.setTimeout(() => {
    if (held.refs > 0 || held.gone) return;
    held.gone = true;
    const byKey = shared.get(vault);
    const key = `${token}\n${blob}`;
    if (byKey?.get(key) === held) byKey.delete(key);
    if (held.url) vault.revoke(held.url);
  }, GRACE_MS);
}

/**
 * One encrypted picture of the chat: fetched (ciphertext), decrypted in memory
 * and shown from an object URL. The URL is revoked when nothing on screen uses
 * it any more, and by the vault when the chat locks or leaves the screen.
 * Nothing decrypted is cached anywhere else (no storage, no service worker).
 */
export function usePrivatePicture(token: string, blob: string, mime: string, vault: PrivateVault, enabled = true): { url: string; failed: boolean } {
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  // Pictures are released when the chat goes off the screen; decrypt again when it is back.
  const epoch = useSyncExternalStore(useCallback((fn: () => void) => vault.subscribe(fn), [vault]), () => vault.epoch);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const held = acquirePicture(vault, token, blob, mime);
    void held.promise
      .then((made) => {
        if (live) setUrl(made);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
      releasePicture(vault, token, blob, held);
    };
  }, [token, blob, mime, vault, epoch, enabled]);
  return { url, failed };
}

/**
 * Whether an element is on or near the screen, so a gallery of thousands of
 * pictures fetches and decrypts only the ones being looked at. Once near, it
 * stays loaded (until the vault releases pictures). Without
 * IntersectionObserver everything counts as near.
 */
export function useNearScreen<T extends Element>(): [(node: T | null) => void, boolean] {
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined');
  const [node, setNode] = useState<T | null>(null);
  useEffect(() => {
    if (near || !node || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setNear(true);
      },
      { rootMargin: '600px 0px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [near, node]);
  return [setNode, near];
}
