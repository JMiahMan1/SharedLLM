import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

const sync = vi.hoisted(() => ({ syncAppInventory: vi.fn().mockResolvedValue(true) }));
vi.mock('../../lib/appInventorySync', () => sync);
const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@capacitor/core')>()),
  Capacitor: { getPlatform: () => platform.name, isNativePlatform: () => platform.name !== 'web' },
}));
const auth = vi.hoisted(() => ({ user: null as Record<string, unknown> | null }));
vi.mock('../../context/AuthContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../context/AuthContext')>()),
  useAuth: () => auth,
}));

import AppInventorySync from './AppInventorySync';

describe('AppInventorySync', () => {
  beforeEach(() => {
    sync.syncAppInventory.mockClear();
    platform.name = 'android';
  });

  it("reports a child's Android device", () => {
    auth.user = { username: 'kid', is_minor: true };
    render(<AppInventorySync />);
    expect(sync.syncAppInventory).toHaveBeenCalledWith('kid');
  });

  it("never reads an adult's device", () => {
    auth.user = { username: 'dad', is_minor: false };
    render(<AppInventorySync />);
    expect(sync.syncAppInventory).not.toHaveBeenCalled();
  });

  it('does nothing off Android', () => {
    auth.user = { username: 'kid', is_minor: true };
    platform.name = 'web';
    render(<AppInventorySync />);
    expect(sync.syncAppInventory).not.toHaveBeenCalled();
  });
});
