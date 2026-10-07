import { registerPlugin } from '@capacitor/core';
import { Capacitor } from '@capacitor/core';

interface LocationTrackingPlugin {
  start(opts: { username: string; userId?: string }): Promise<{ started: boolean }>;
  stop(): Promise<void>;
  isRunning(): Promise<{ running: boolean }>;
  batteryUnrestricted(): Promise<{ unrestricted: boolean }>;
  requestBatteryUnrestricted(): Promise<{ unrestricted: boolean }>;
}

/**
 * Background location lives in a native foreground service.
 *
 * The WebView's `watchPosition` only exists while the JS context does, so
 * tracking that depends on it silently stops when Android reclaims the process
 * -- observed in production as location *and* steps going quiet at the same
 * instant. This plugin hands the job to a service that survives that.
 *
 * Feature-detected: a web bundle can be newer than the installed APK, so an
 * older build reports the plugin as missing rather than throwing, and callers
 * fall back to the WebView watch (which at least works while the app is open).
 */
export function getLocationTracking(): LocationTrackingPlugin | null {
  if (!Capacitor.isNativePlatform()) return null;
  try {
    return registerPlugin<LocationTrackingPlugin>('LocationTracking');
  } catch (err) {
    console.warn('[locationTracking] plugin unavailable:', err);
    return null;
  }
}

export interface BackgroundTrackingState {
  /** True when a native service is carrying the upload. */
  serviceRunning: boolean;
  /** True when this build can start one at all. */
  supported: boolean;
}

export async function startBackgroundTracking(
  username: string,
  userId?: string,
): Promise<BackgroundTrackingState> {
  const plugin = getLocationTracking();
  if (!plugin) return { serviceRunning: false, supported: false };
  try {
    await plugin.start({ username, userId });
    return { serviceRunning: true, supported: true };
  } catch (err) {
    // Never let this break tracking in the app itself -- the WebView watch can
    // still work while the app is in front, which beats not tracking at all.
    console.warn('[locationTracking] start failed:', err);
    return { serviceRunning: false, supported: true };
  }
}

export async function stopBackgroundTracking(): Promise<void> {
  const plugin = getLocationTracking();
  if (!plugin) return;
  try {
    await plugin.stop();
  } catch (err) {
    console.warn('[locationTracking] stop failed:', err);
  }
}

export async function isBackgroundTrackingRunning(): Promise<boolean> {
  const plugin = getLocationTracking();
  if (!plugin) return false;
  try {
    const res = await plugin.isRunning();
    return Boolean(res?.running);
  } catch {
    return false;
  }
}

/** Mirror the signed-in identity natively so the service can attribute uploads. */
export async function mirrorIdentity(username: string, userId?: string): Promise<void> {
  if (!Capacitor.isNativePlatform()) return;
  try {
    const { default: TokenBridge } = await import('../plugins/tokenBridge');
    await TokenBridge.setIdentity({ username, userId });
  } catch (err) {
    console.warn('[locationTracking] could not mirror identity:', err);
  }
}

/**
 * Whether Android's battery optimisation leaves location sharing alone.
 * `null` when it cannot say: not Android, or an APK older than the check.
 * While restricted, Doze stops location for hours once the phone lies still
 * with the screen off.
 */
export async function isBatteryUnrestricted(): Promise<boolean | null> {
  const plugin = getLocationTracking();
  if (!plugin) return null;
  try {
    const res = await plugin.batteryUnrestricted();
    return Boolean(res?.unrestricted);
  } catch {
    return null;
  }
}

/** Opens Android's own "allow in background" dialog. */
export async function requestBatteryUnrestricted(): Promise<void> {
  const plugin = getLocationTracking();
  if (!plugin) return;
  try {
    await plugin.requestBatteryUnrestricted();
  } catch (err) {
    console.warn('[locationTracking] battery exemption request failed:', err);
  }
}
