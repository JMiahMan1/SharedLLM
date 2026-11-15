import { Capacitor, registerPlugin } from '@capacitor/core';

/**
 * Android's FLAG_SECURE for the app window: no screenshots, no screen
 * recording, and a blank thumbnail in the recent apps list. On while the
 * parents' private chat is open, off as soon as it closes.
 *
 * Newer APKs only. An older APK has no such plugin and its calls reject: the
 * private chat then says that screenshots are not blocked on this version,
 * rather than pretending they are.
 */
export interface SecureWindowPluginInterface {
  enable(): Promise<{ secure: boolean }>;
  disable(): Promise<{ secure: boolean }>;
}

const SecureWindow = registerPlugin<SecureWindowPluginInterface>('JarvisSecureWindow');

/** 'on' when blocked, 'unsupported' on an APK without the plugin, 'web' in a browser (nothing to block). */
export type SecureState = 'on' | 'unsupported' | 'web';

export async function secureWindowOn(): Promise<SecureState> {
  if (!Capacitor.isNativePlatform()) return 'web';
  try {
    const { secure } = await SecureWindow.enable();
    return secure ? 'on' : 'unsupported';
  } catch {
    return 'unsupported';
  }
}

export async function secureWindowOff(): Promise<void> {
  if (!Capacitor.isNativePlatform()) return;
  try {
    await SecureWindow.disable();
  } catch {
    // An APK without the plugin never turned it on.
  }
}

export default SecureWindow;
