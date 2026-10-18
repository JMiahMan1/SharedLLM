import { storageGet, storageSet } from './storage';

/** Per-device opt-in for silently answering a family drop-in call. */
export const AUTO_DROP_IN_KEY = 'jarvis_auto_drop_in';

/** Fired after the setting changes so the call provider does not wait for a poll. */
export const AUTO_DROP_IN_EVENT = 'jarvis-auto-drop-in-changed';

export async function getAutoDropIn(): Promise<boolean> {
  return (await storageGet(AUTO_DROP_IN_KEY)) === '1';
}

export async function setAutoDropIn(enabled: boolean): Promise<void> {
  await storageSet(AUTO_DROP_IN_KEY, enabled ? '1' : '0');
  window.dispatchEvent(new Event(AUTO_DROP_IN_EVENT));
}
