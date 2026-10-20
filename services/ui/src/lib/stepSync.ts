import { Capacitor, registerPlugin } from '@capacitor/core';

interface StepSyncPlugin {
  start(opts: { username: string; userId?: string }): Promise<{ started: boolean }>;
  stop(): Promise<void>;
  isRunning(): Promise<{ running: boolean }>;
}

/**
 * Background step sync lives in a native foreground service, separate from
 * location. The WebView pedometer only reads while the app is open; this keeps
 * the on-device ledger current so a phone that is not opened still reports its
 * day to `/api/geo/steps`.
 *
 * Feature-detected because an OTA bundle can be newer than the installed APK.
 */
export function getStepSync(): StepSyncPlugin | null {
  if (!Capacitor.isNativePlatform()) return null;
  try {
    return registerPlugin<StepSyncPlugin>('StepSync');
  } catch (err) {
    console.warn('[stepSync] plugin unavailable:', err);
    return null;
  }
}

export interface BackgroundStepSyncState {
  serviceRunning: boolean;
  supported: boolean;
}

export async function startBackgroundSteps(
  username: string,
  userId?: string,
): Promise<BackgroundStepSyncState> {
  const plugin = getStepSync();
  if (!plugin) return { serviceRunning: false, supported: false };
  try {
    await plugin.start({ username, userId });
    return { serviceRunning: true, supported: true };
  } catch (err) {
    console.warn('[stepSync] start failed:', err);
    return { serviceRunning: false, supported: true };
  }
}

export async function stopBackgroundSteps(): Promise<void> {
  const plugin = getStepSync();
  if (!plugin) return;
  try {
    await plugin.stop();
  } catch (err) {
    console.warn('[stepSync] stop failed:', err);
  }
}

export async function isBackgroundStepsRunning(): Promise<boolean> {
  const plugin = getStepSync();
  if (!plugin) return false;
  try {
    const res = await plugin.isRunning();
    return Boolean(res?.running);
  } catch {
    return false;
  }
}
