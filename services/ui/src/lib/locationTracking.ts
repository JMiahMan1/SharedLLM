import { registerPlugin } from '@capacitor/core';
import { Capacitor } from '@capacitor/core';

interface LocationTrackingPlugin {
  start(opts: { username: string; userId?: string; steps?: boolean }): Promise<{ started: boolean }>;
  stop(): Promise<void>;
  isRunning(): Promise<{ running: boolean }>;
  batteryUnrestricted(): Promise<{ unrestricted: boolean }>;
  requestBatteryUnrestricted(): Promise<{ unrestricted: boolean }>;
  backgroundLocation(): Promise<{ granted: boolean; foreground: boolean }>;
  requestBackgroundLocation(): Promise<{ granted: boolean }>;
  hasActivityAccess(): Promise<{ granted: boolean }>;
  requestActivityAccess(): Promise<{ granted: boolean }>;
  crashStatus(): Promise<{ pending: boolean; sendsAt: number | null }>;
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

/**
 * Start (or update) the native service. `steps` lets it read the pedometer
 * with the app closed and send the day's count with each upload; it follows
 * the Steps sensor switch, so calling this again after that switch changes
 * updates the running service.
 */
export async function startBackgroundTracking(
  username: string,
  opts: { userId?: string; steps?: boolean } = {},
): Promise<BackgroundTrackingState> {
  const plugin = getLocationTracking();
  if (!plugin) return { serviceRunning: false, supported: false };
  try {
    await plugin.start({ username, userId: opts.userId, steps: Boolean(opts.steps) });
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

/**
 * Whether location may be read with the app closed ("Allow all the time").
 * `null` when it cannot say: not Android, or an APK older than the check.
 * With only "While using the app", sharing stops for good the first time
 * Android reclaims the app, with no error anywhere.
 */
export async function hasBackgroundLocation(): Promise<boolean | null> {
  const plugin = getLocationTracking();
  if (!plugin) return null;
  try {
    const res = await plugin.backgroundLocation();
    return Boolean(res?.granted);
  } catch {
    return null;
  }
}

/** Takes the user to Android's own "Allow all the time" choice for this app. */
export async function requestBackgroundLocation(): Promise<void> {
  const plugin = getLocationTracking();
  if (!plugin) return;
  try {
    await plugin.requestBackgroundLocation();
  } catch (err) {
    console.warn('[locationTracking] background location request failed:', err);
  }
}

/**
 * Whether Activity Recognition is allowed, which lets the service tell driving
 * (a fix every second) from sitting still (no GPS). `null` when it cannot say:
 * not Android, or an APK older than the check. Without it the service still
 * finds drives, by speed alone.
 */
export async function hasActivityAccess(): Promise<boolean | null> {
  const plugin = getLocationTracking();
  if (!plugin) return null;
  try {
    const res = await plugin.hasActivityAccess();
    return Boolean(res?.granted);
  } catch {
    return null;
  }
}

/**
 * Shows Android's own "Physical activity" prompt and resolves with the answer.
 * `null` when it cannot ask: not Android, or an APK older than the request.
 */
export async function requestActivityAccess(): Promise<boolean | null> {
  const plugin = getLocationTracking();
  if (!plugin) return null;
  try {
    const res = await plugin.requestActivityAccess();
    return Boolean(res?.granted);
  } catch (err) {
    console.warn('[locationTracking] activity recognition request failed:', err);
    return null;
  }
}

export interface CrashStatus {
  /** A crash alert is counting down or being sent to the family. */
  pending: boolean;
  /** When the countdown runs out (ms since the epoch); null once it has, or with none. */
  sendsAt: number | null;
}

/**
 * Whether the phone's crash detection has an alert counting down or being
 * sent. Not Android, or an APK older than crash detection: nothing pending.
 */
export async function getCrashStatus(): Promise<CrashStatus> {
  const plugin = getLocationTracking();
  if (!plugin) return { pending: false, sendsAt: null };
  try {
    const res = await plugin.crashStatus();
    return {
      pending: Boolean(res?.pending),
      sendsAt: typeof res?.sendsAt === 'number' ? res.sendsAt : null,
    };
  } catch {
    return { pending: false, sendsAt: null };
  }
}
