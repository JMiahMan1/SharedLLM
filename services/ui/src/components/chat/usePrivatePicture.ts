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
export function usePrivatePicture(token: string, blob: string, mime: string, vault: PrivateVault): { url: string; failed: boolean } {
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  // Pictures are released when the chat goes off the screen; decrypt again when it is back.
  const epoch = useSyncExternalStore(useCallback((fn: () => void) => vault.subscribe(fn), [vault]), () => vault.epoch);
  useEffect(() => {
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
  }, [token, blob, mime, vault, epoch]);
  return { url, failed };
}
