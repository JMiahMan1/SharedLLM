/**
 * The page's side of "Unlock with this phone's lock" (see plugins/deviceUnlock.ts).
 *
 * Turning it on needs the chat password once more: the data key is recovered
 * as bytes only for that moment, handed to the Android plugin (which encrypts
 * it under a Keystore key behind the phone's lock) and overwritten with zeros.
 * Unlocking takes the bytes back from the plugin, imports them as a
 * non-extractable key and zeroes them. A password change does not touch it:
 * the data key stays the same.
 */
import type { DeviceUnlockPluginInterface } from '../plugins/deviceUnlock';
import { checkVerifier, fromBase64, importDataKey, rawDataKey, toBase64, WrongPasswordError, type ChatKeyInfo, type Sealed } from './privateChatCrypto';

/** Turn the phone lock on for a chat on this device, after checking the chat password. */
export async function enableDeviceUnlock(
  plugin: DeviceUnlockPluginInterface,
  token: string,
  password: string,
  info: ChatKeyInfo,
): Promise<void> {
  await giveKeyTo(plugin, token, password, info);
}

/**
 * The chat's key from the phone's lock: imported never extractable, its bytes
 * zeroed. Throws (the plugin's reason) when the person cancels or the phone's
 * key is gone; the lock screen then offers the password.
 */
export async function keyFromDevice(plugin: DeviceUnlockPluginInterface, token: string, verifier: Sealed): Promise<CryptoKey> {
  const { key } = await plugin.unlock({ token });
  const chatKey = await importDataKey(fromBase64(key));
  if (!(await checkVerifier(chatKey, verifier))) {
    // The verifier never changes, so the phone holds the wrong key: forget it.
    await plugin.disable({ token }).catch(() => {});
    throw new Error('The key on this phone does not open this chat. Use the password, then set the phone lock up again.');
  }
  return chatKey;
}

/** Anything that keeps the chat key on this phone given the bytes (phone lock, notification previews). */
interface KeyKeeper {
  enable(options: { token: string; key: string }): Promise<{ enabled: boolean }>;
}

/**
 * Hand the chat's data key to a native keeper after checking the password,
 * zeroing the bytes afterwards (the phone lock, notification previews).
 */
export async function giveKeyTo(keeper: KeyKeeper, token: string, password: string, info: ChatKeyInfo): Promise<void> {
  const raw = await rawDataKey(password, info);
  if (!raw) throw new WrongPasswordError('That is not the password for this chat.');
  try {
    const { enabled } = await keeper.enable({ token, key: toBase64(raw.dk) });
    if (!enabled) throw new Error('It was not turned on.');
  } finally {
    raw.dk.fill(0);
  }
}
