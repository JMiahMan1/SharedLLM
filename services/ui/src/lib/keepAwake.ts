import { Capacitor } from '@capacitor/core';

/**
 * Keeping the screen on while a person walks a room with the phone: a
 * Capacitor KeepAwake plugin when the app build has one, else the browser's
 * Screen Wake Lock API, else nothing (the caller shows a note saying so).
 */
export interface AwakeHold {
  how: 'keep-awake' | 'wake-lock';
  release(): Promise<void>;
}

interface KeepAwakePlugin {
  keepAwake(): Promise<void>;
  allowSleep(): Promise<void>;
}

interface WakeLockSentinelLike {
  release(): Promise<void>;
}

export async function holdScreenOn(): Promise<AwakeHold | null> {
  const plugins = (Capacitor as unknown as { Plugins?: Record<string, unknown> }).Plugins;
  if (Capacitor.isPluginAvailable?.('KeepAwake') && plugins?.KeepAwake) {
    const plugin = plugins.KeepAwake as KeepAwakePlugin;
    try {
      await plugin.keepAwake();
      return { how: 'keep-awake', release: () => plugin.allowSleep() };
    } catch {
      // fall through to the browser's own lock
    }
  }
  const wakeLock = (navigator as unknown as { wakeLock?: { request(type: 'screen'): Promise<WakeLockSentinelLike> } }).wakeLock;
  if (wakeLock) {
    try {
      const sentinel = await wakeLock.request('screen');
      return { how: 'wake-lock', release: () => sentinel.release() };
    } catch {
      return null; // refused (page hidden, battery saver): the caller says so
    }
  }
  return null;
}
