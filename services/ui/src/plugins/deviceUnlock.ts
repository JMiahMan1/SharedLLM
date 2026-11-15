import { Capacitor, registerPlugin } from '@capacitor/core';

/**
 * "Unlock with this phone's lock" for the parents' private chat (Android).
 *
 * The native side (JarvisDeviceUnlock, DeviceUnlockPlugin.java) keeps one
 * Android Keystore AES-256-GCM key per chat that only works right after the
 * person passes the phone's own lock (fingerprint, face, PIN, pattern or
 * password), and is destroyed when a new fingerprint is enrolled or the lock
 * screen is removed. It stores the chat's data key encrypted under that key,
 * in the app's private storage; the plain key is never stored anywhere.
 *
 * Only on an APK with the plugin. The web build (and an older APK) never
 * offers it, and nothing ever stores the key anywhere else instead.
 */
export interface DeviceUnlockPluginInterface {
  available(): Promise<{ available: boolean; reason?: string }>;
  isEnabled(options: { token: string }): Promise<{ enabled: boolean }>;
  /** Asks for the phone's lock, then keeps `key` (base64 data-key bytes) encrypted under the Keystore key. */
  enable(options: { token: string; key: string }): Promise<{ enabled: boolean }>;
  /** Asks for the phone's lock and gives back the data key (base64). */
  unlock(options: { token: string }): Promise<{ key: string }>;
  disable(options: { token: string }): Promise<{ enabled: boolean }>;
}

const registered = registerPlugin<DeviceUnlockPluginInterface>('JarvisDeviceUnlock');

declare global {
  interface Window {
    /** Hermetic browser tests only (the dev server): stands in for the Android plugin. */
    __jarvisDeviceUnlockMock?: DeviceUnlockPluginInterface;
  }
}

/** The plugin, or null where there is none (the web build, an older APK). */
export function deviceUnlockPlugin(): DeviceUnlockPluginInterface | null {
  if (import.meta.env.DEV && typeof window !== 'undefined' && window.__jarvisDeviceUnlockMock) {
    return window.__jarvisDeviceUnlockMock;
  }
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android' ? registered : null;
}

/** Whether this device can offer the phone lock at all (never on the web). */
export async function deviceUnlockAvailable(plugin = deviceUnlockPlugin()): Promise<boolean> {
  if (!plugin) return false;
  try {
    return Boolean((await plugin.available()).available);
  } catch {
    // An APK without the plugin: not offered.
    return false;
  }
}

/** Whether the phone lock is on for this chat on this device. */
export async function deviceUnlockEnabled(token: string, plugin = deviceUnlockPlugin()): Promise<boolean> {
  if (!plugin) return false;
  try {
    return Boolean((await plugin.isEnabled({ token })).enabled);
  } catch {
    return false;
  }
}

/** The plugin's reason for a failure: its message, else the fallback. */
export function deviceUnlockError(error: unknown, fallback: string): string {
  const message = (error as { message?: unknown })?.message;
  return typeof message === 'string' && message ? message : fallback;
}

export default registered;
