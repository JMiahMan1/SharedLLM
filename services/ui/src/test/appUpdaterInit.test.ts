import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

/**
 * Regression test for the OTA guard fix (0afd1da6): a native APK upgrade ships
 * its own web layer, so an OTA bundle staged under the previous build (and any
 * version blacklisted because it could not match that old layer) must be
 * dropped BEFORE the pending/rejected reconciliation runs. Otherwise every APK
 * upgrade blacklists a perfectly good server version and refuses the next
 * update ("Update on the server is mislabelled...").
 */

const mocks = vi.hoisted(() => ({
  isNative: vi.fn(() => true),
  getInfo: vi.fn(async () => ({ build: '25' })),
  notifyAppReady: vi.fn(async () => undefined),
  reset: vi.fn(async () => undefined),
}));

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: mocks.isNative },
  registerPlugin: vi.fn(),
}));
vi.mock('@capacitor/app', () => ({ App: { getInfo: mocks.getInfo } }));
vi.mock('@capgo/capacitor-updater', () => ({
  CapacitorUpdater: { notifyAppReady: mocks.notifyAppReady, reset: mocks.reset },
}));
vi.mock('../lib/serverUrl', () => ({ getServerOrigin: () => 'https://jarvis.example.com' }));

const PENDING_KEY = 'jarvis_ota_pending_sha';
const REJECTED_KEY = 'jarvis_ota_rejected_shas';
const LAST_BUILD_KEY = 'jarvis_last_native_build';

/** A /version.json response naming the SHA the running bundle really carries. */
const runningBundle = (gitSha: string) => ({
  ok: true,
  json: async () => ({ version: '1.5.2', gitSha }),
});

describe('initAppUpdater OTA guard on a native upgrade (0afd1da6)', () => {
  beforeEach(() => {
    // The module has a module-level `isInitialized` guard, so re-import it fresh
    // for every test instead of letting the first run win.
    vi.resetModules();
    vi.clearAllMocks();
    vi.useFakeTimers();
    localStorage.clear();
    mocks.isNative.mockReturnValue(true);
    mocks.getInfo.mockResolvedValue({ build: '25' });
    mocks.notifyAppReady.mockResolvedValue(undefined);
    mocks.reset.mockResolvedValue(undefined);
    vi.stubGlobal('fetch', vi.fn(async () => runningBundle('aaaa1111')));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('clears a stale pending and rejected OTA SHA when the native build number increases', async () => {
    localStorage.setItem(LAST_BUILD_KEY, '24');
    localStorage.setItem(PENDING_KEY, 'bbbb2222');
    localStorage.setItem(REJECTED_KEY, JSON.stringify(['bbbb2222']));

    const { initAppUpdater } = await import('../lib/appUpdater');
    await initAppUpdater();

    // The native-build change drops the OTA bundle staged under the old APK.
    expect(localStorage.getItem(PENDING_KEY)).toBeNull();
    expect(localStorage.getItem(REJECTED_KEY)).toBeNull();
    // Crucially, the stale pending SHA is NOT blacklisted on the way out.
    expect(JSON.parse(localStorage.getItem(REJECTED_KEY) ?? '[]')).not.toContain('bbbb2222');
    expect(localStorage.getItem(LAST_BUILD_KEY)).toBe('25');
    expect(mocks.notifyAppReady).toHaveBeenCalled();
    expect(mocks.reset).toHaveBeenCalledWith({ toLastSuccessful: false });
  });

  it('still blacklists a mismatched bundle when the native build did not change', async () => {
    localStorage.setItem(LAST_BUILD_KEY, '25');
    localStorage.setItem(PENDING_KEY, 'bbbb2222');

    const { initAppUpdater } = await import('../lib/appUpdater');
    await initAppUpdater();

    expect(localStorage.getItem(PENDING_KEY)).toBeNull();
    expect(JSON.parse(localStorage.getItem(REJECTED_KEY) ?? '[]')).toContain('bbbb2222');
    expect(mocks.reset).not.toHaveBeenCalled();
  });
});
