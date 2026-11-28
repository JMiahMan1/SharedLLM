import type { PrivateVault } from '../../lib/privateChatCrypto';
import { usePrivatePicture } from './usePrivatePicture';

/**
 * One encrypted voice note of the chat: fetched as ciphertext, decrypted in
 * memory and played from an object URL.
 *
 * A recording is one sealed blob, exactly like a picture, so it goes through
 * the same hook rather than a second copy of it: one decrypt per blob (the
 * bubble and any viewer share it), revoked when nothing on screen uses it any
 * more, and by the vault when the chat locks or leaves the screen. Nothing
 * decrypted is written anywhere.
 */
export function usePrivateAudio(
  token: string,
  blob: string,
  mime: string,
  vault: PrivateVault,
  enabled = true,
): { url: string; failed: boolean } {
  return usePrivatePicture(token, blob, mime, vault, enabled);
}
