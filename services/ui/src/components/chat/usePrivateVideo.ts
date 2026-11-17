import { useEffect, useState } from 'react';
import { api } from '../../services/api';
import type { PrivateVault, VideoPayload } from '../../lib/privateChatCrypto';

/**
 * Decrypt a clip into an in-memory object URL while `video` is open; the URL
 * is revoked when it closes (and by the vault on lock), so nothing decrypted
 * outlives the player.
 */
export function usePrivateVideo(token: string, video: VideoPayload | null, vault: PrivateVault): { url: string; failed: string; progress: { done: number; count: number } } {
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState('');
  const [progress, setProgress] = useState({ done: 0, count: video?.chunks.length ?? 0 });
  useEffect(() => {
    if (!video) return;
    let live = true;
    let made = '';
    void vault
      .videoUrl(video, (id) => api.getPrivateBlob(token, id), (done, count) => live && setProgress({ done, count }))
      .then((result) => {
        made = result;
        if (live) setUrl(result);
        else vault.revoke(result);
      })
      .catch((error: unknown) => {
        if (live) setFailed(error instanceof Error && error.message ? error.message : 'This video could not be decrypted.');
      });
    return () => {
      live = false;
      if (made) vault.revoke(made);
      setUrl('');
      setFailed('');
    };
  }, [token, video, vault]);
  return { url, failed, progress };
}
