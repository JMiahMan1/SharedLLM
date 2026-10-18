import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AUTO_DROP_IN_EVENT,
  AUTO_DROP_IN_KEY,
  getAutoDropIn,
  setAutoDropIn,
} from '../lib/callSettings';

describe('auto drop-in setting', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('is off until this device opts in', async () => {
    expect(await getAutoDropIn()).toBe(false);
  });

  it('stores the choice and tells the call provider', async () => {
    const changed = vi.fn();
    window.addEventListener(AUTO_DROP_IN_EVENT, changed);

    try {
      await setAutoDropIn(true);

      expect(localStorage.getItem(AUTO_DROP_IN_KEY)).toBe('1');
      expect(await getAutoDropIn()).toBe(true);
      expect(changed).toHaveBeenCalledTimes(1);

      await setAutoDropIn(false);

      expect(localStorage.getItem(AUTO_DROP_IN_KEY)).toBe('0');
      expect(await getAutoDropIn()).toBe(false);
      expect(changed).toHaveBeenCalledTimes(2);
    } finally {
      window.removeEventListener(AUTO_DROP_IN_EVENT, changed);
    }
  });
});
