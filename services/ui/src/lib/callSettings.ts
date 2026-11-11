import { storageGet, storageRemove, storageSet } from './storage';

/**
 * Family drop-in has two switches:
 *
 * - Per account, on the server: who may drop in on this person. Adults choose
 *   their own; a parent chooses a child's or teen's.
 * - Per device, also on the server: whether this phone or tablet answers an
 *   allowed drop-in by itself. A parent may switch a child's device.
 *
 * The device's own answer is copied here on every call-list poll, so the
 * Android side (DropInCall.java) can decide in the background without asking.
 */
export const DEVICE_DROP_IN_KEY = 'jarvis_drop_in_device';
/** The old per-device opt-in, before drop-in was per account and per device. */
export const LEGACY_AUTO_DROP_IN_KEY = 'jarvis_auto_drop_in';

/** Fired after the device switch changes so the call provider does not wait for a poll. */
export const AUTO_DROP_IN_EVENT = 'jarvis-auto-drop-in-changed';

/** Whether this device answers allowed drop-ins by itself (on unless switched off). */
export async function getDeviceDropIn(): Promise<boolean> {
  return (await storageGet(DEVICE_DROP_IN_KEY)) !== '0';
}

/** Store this device's switch as the server reported it. */
export async function storeDeviceDropIn(enabled: boolean): Promise<void> {
  const next = enabled ? '1' : '0';
  if ((await storageGet(DEVICE_DROP_IN_KEY)) === next) return;
  await storageSet(DEVICE_DROP_IN_KEY, next);
  window.dispatchEvent(new Event(AUTO_DROP_IN_EVENT));
}

/** The old switch: "1" when this device used to answer any family ring. */
export async function readLegacyAutoDropIn(): Promise<string | null> {
  return storageGet(LEGACY_AUTO_DROP_IN_KEY);
}

/**
 * Forget the old switch once its choice is carried over, so an older Android
 * build never answers a ring the per-person list does not allow.
 */
export async function clearLegacyAutoDropIn(): Promise<void> {
  await storageRemove(LEGACY_AUTO_DROP_IN_KEY);
}
