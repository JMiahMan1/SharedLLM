import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AUTO_DROP_IN_EVENT,
  DEVICE_DROP_IN_KEY,
  LEGACY_AUTO_DROP_IN_KEY,
  clearLegacyAutoDropIn,
  getDeviceDropIn,
  readLegacyAutoDropIn,
  storeDeviceDropIn,
} from '../lib/callSettings';

describe('this device answering drop-ins', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('is on until switched off (the person\'s list decides who may drop in)', async () => {
    expect(await getDeviceDropIn()).toBe(true);
  });

  it('stores what the server says and tells the call provider only on a change', async () => {
    const changed = vi.fn();
    window.addEventListener(AUTO_DROP_IN_EVENT, changed);
    try {
      await storeDeviceDropIn(false);
      expect(localStorage.getItem(DEVICE_DROP_IN_KEY)).toBe('0');
      expect(await getDeviceDropIn()).toBe(false);
      await storeDeviceDropIn(false);
      expect(changed).toHaveBeenCalledTimes(1);
      await storeDeviceDropIn(true);
      expect(await getDeviceDropIn()).toBe(true);
      expect(changed).toHaveBeenCalledTimes(2);
    } finally {
      window.removeEventListener(AUTO_DROP_IN_EVENT, changed);
    }
  });

  it('reads and then forgets the old per-device switch', async () => {
    localStorage.setItem(LEGACY_AUTO_DROP_IN_KEY, '1');
    expect(await readLegacyAutoDropIn()).toBe('1');
    await clearLegacyAutoDropIn();
    expect(await readLegacyAutoDropIn()).toBeNull();
  });
});
