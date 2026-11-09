/**
 * Which device a location upload comes from.
 *
 * Someone signed in on a phone and a tablet uploads from both, and the server
 * lets only one of them say where they are (identity's location_devices). In
 * the Android app the id is the install's own (the background location
 * service sends the same one, so the open app is not a second device); in a
 * browser it is a random id kept in local storage.
 */
import { Capacitor } from '@capacitor/core';
import TokenBridge from '../plugins/tokenBridge';

export interface LocationDevice {
  deviceId: string;
  deviceName: string;
}

const STORAGE_KEY = 'jarvis_location_device_id';

let cached: Promise<LocationDevice> | null = null;

function randomId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** A browser's id: made once, then reused. Storage can be unavailable (private mode). */
export function browserDeviceId(storage: Pick<Storage, 'getItem' | 'setItem'> | null): string {
  try {
    const held = storage?.getItem(STORAGE_KEY);
    if (held) return held;
    const made = randomId();
    storage?.setItem(STORAGE_KEY, made);
    return made;
  } catch {
    return randomId();
  }
}

async function resolve(): Promise<LocationDevice> {
  if (Capacitor.isNativePlatform()) {
    try {
      const { deviceId, deviceName } = await TokenBridge.getDeviceId();
      if (deviceId) return { deviceId, deviceName: deviceName || 'Android' };
    } catch {
      // An older app shell without getDeviceId: fall through to a stored id.
    }
  }
  const storage = typeof window !== 'undefined' ? window.localStorage : null;
  return { deviceId: browserDeviceId(storage), deviceName: 'Web browser' };
}

/** This device, resolved once per page load. */
export function locationDevice(): Promise<LocationDevice> {
  if (!cached) cached = resolve();
  return cached;
}

/** Tests only. */
export function resetLocationDeviceForTests(): void {
  cached = null;
}
