import { Capacitor, registerPlugin } from '@capacitor/core';

/**
 * "Show message text in notifications" for the private chat (Android only,
 * off by default, per chat and per phone). PrivatePreviewPlugin.java keeps the
 * chat's key under a Keystore key that does NOT need the phone's lock, so the
 * push service can open a message in the background: weaker than the chat
 * itself, which the toggle says. The text is never shown on the lock screen.
 * The web build never offers it, and nothing else ever stores the key.
 */
export interface PrivatePreviewPluginInterface {
  available(): Promise<{ available: boolean }>;
  isEnabled(options: { token: string }): Promise<{ enabled: boolean }>;
  enable(options: { token: string; key: string }): Promise<{ enabled: boolean }>;
  disable(options: { token: string }): Promise<{ enabled: boolean }>;
}

const registered = registerPlugin<PrivatePreviewPluginInterface>('JarvisPrivatePreview');

declare global {
  interface Window {
    /** Hermetic browser tests only (the dev server): stands in for the Android plugin. */
    __jarvisPrivatePreviewMock?: PrivatePreviewPluginInterface;
  }
}

export function privatePreviewPlugin(): PrivatePreviewPluginInterface | null {
  if (import.meta.env.DEV && typeof window !== 'undefined' && window.__jarvisPrivatePreviewMock) {
    return window.__jarvisPrivatePreviewMock;
  }
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android' ? registered : null;
}

export async function privatePreviewState(token: string, plugin = privatePreviewPlugin()): Promise<{ available: boolean; enabled: boolean }> {
  if (!plugin) return { available: false, enabled: false };
  try {
    if (!(await plugin.available()).available) return { available: false, enabled: false };
    return { available: true, enabled: Boolean((await plugin.isEnabled({ token })).enabled) };
  } catch {
    // An APK without the plugin: not offered.
    return { available: false, enabled: false };
  }
}

export default registered;
