import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { api } from '../../services/api';
import type { PrivateVault } from '../../lib/privateChatCrypto';

/** Stop the browser offering to save, share, drag or open a picture elsewhere. */
export const noSave = {
  draggable: false,
  onContextMenu: (event: { preventDefault: () => void }) => event.preventDefault(),
  onDragStart: (event: { preventDefault: () => void }) => event.preventDefault(),
};

/**
 * One encrypted picture of the chat: fetched (ciphertext), decrypted in memory
 * and shown from an object URL the vault revokes when the chat locks or leaves
 * the screen. Nothing decrypted is cached anywhere else.
 */
export function usePrivatePicture(token: string, blob: string, mime: string, vault: PrivateVault, enabled = true): { url: string; failed: boolean } {
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  // Pictures are released when the chat goes off the screen; decrypt again when it is back.
  const epoch = useSyncExternalStore(useCallback((fn: () => void) => vault.subscribe(fn), [vault]), () => vault.epoch);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    void api
      .getPrivateBlob(token, blob)
      .then((bytes) => vault.pictureUrl(bytes, mime))
      .then((made) => {
        if (live) setUrl(made);
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
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
