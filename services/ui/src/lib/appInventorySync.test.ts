import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const phoneUsage = vi.hoisted(() => ({ getInstalledApps: vi.fn() }));
vi.mock('../plugins/phoneUsage', () => ({ default: phoneUsage }));

import { syncAppInventory } from './appInventorySync';

const APP = { package: 'com.example.game', label: 'Game', installer: 'com.android.vending', first_install: 1,
  last_update: 2, launchable: true, permissions: ['camera'], last_used: 3, size_bytes: 4 };
const NOW = 1_800_000_000_000;

describe('syncAppInventory', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('jarvis_api_key', 'test-key');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true }));
    phoneUsage.getInstalledApps.mockResolvedValue({ apps: [APP], usageAccess: true });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('posts the list with this device key, then waits for a change or a day', async () => {
    expect(await syncAppInventory('kid', NOW)).toBe(true);
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/apps/inventory');
    const body = JSON.parse(String(init.body));
    expect(body.apps).toEqual([APP]);
    expect(body.usage_access).toBe(true);
    expect(body.device_key).toMatch(/^[0-9a-f]{16,}$/);
    expect(await syncAppInventory('kid', NOW + 60_000)).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(await syncAppInventory('kid', NOW + 86_400_000)).toBe(true);
  });

  it('sends again when an app is added', async () => {
    await syncAppInventory('kid', NOW);
    phoneUsage.getInstalledApps.mockResolvedValue({ apps: [APP, { ...APP, package: 'com.example.other' }], usageAccess: true });
    expect(await syncAppInventory('kid', NOW + 60_000)).toBe(true);
  });

  it('a refused report is tried again next time', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({ ok: false } as Response);
    expect(await syncAppInventory('kid', NOW)).toBe(false);
    expect(await syncAppInventory('kid', NOW + 60_000)).toBe(true);
  });

  it('stays quiet signed out or on an APK without app checks', async () => {
    localStorage.removeItem('jarvis_api_key');
    expect(await syncAppInventory('kid', NOW)).toBe(false);
    localStorage.setItem('jarvis_api_key', 'test-key');
    phoneUsage.getInstalledApps.mockRejectedValue(new Error('not implemented'));
    expect(await syncAppInventory('kid', NOW)).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });
});
