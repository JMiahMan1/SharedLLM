import { describe, it, expect, beforeEach } from 'vitest';
import { setAdminPin, verifyAdminPin, clearAdminPin, isAdminPinSet, getAdminPinHash } from './adminPin';

describe('adminPin', () => {
  beforeEach(() => {
    clearAdminPin();
  });

  it('rejects verification when no PIN is set', async () => {
    expect(isAdminPinSet()).toBe(false);
    expect(await verifyAdminPin('1234')).toBe(false);
  });

  it('verifies a set PIN and rejects wrong ones', async () => {
    await setAdminPin('1234');
    expect(isAdminPinSet()).toBe(true);
    expect(await verifyAdminPin('1234')).toBe(true);
    expect(await verifyAdminPin('0000')).toBe(false);
    expect(await verifyAdminPin('123')).toBe(false);
  });

  it('stores a hashed value, never the plaintext', async () => {
    await setAdminPin('5678');
    const stored = getAdminPinHash();
    expect(stored).not.toBe('5678');
    expect(stored).toMatch(/^[0-9a-f]{64}$/);
  });

  it('clearing removes the PIN', async () => {
    await setAdminPin('9999');
    clearAdminPin();
    expect(isAdminPinSet()).toBe(false);
    expect(await verifyAdminPin('9999')).toBe(false);
  });
});
