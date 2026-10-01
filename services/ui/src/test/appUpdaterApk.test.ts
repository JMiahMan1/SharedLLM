import { describe, it, expect, vi, beforeEach } from 'vitest';
import { checkApkUpdate } from '../lib/appUpdater';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  isNative: vi.fn(() => true),
  getInfo: vi.fn(async () => ({ build: '21' })),
  origin: 'https://jarvis.example.com',
}));

vi.mock('axios', () => ({ default: { get: mocks.get } }));
vi.mock('../lib/serverUrl', () => ({ getServerOrigin: () => mocks.origin }));
vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: mocks.isNative } }));
vi.mock('@capacitor/app', () => ({ App: { getInfo: mocks.getInfo } }));

const remote = (over: Record<string, unknown> = {}) => ({
  version: '1.5.0',
  git_sha: 'abc1234',
  bundle_available: true,
  apk_available: true,
  apk_url: '/api/app-updates/app-debug.apk',
  apk_size_bytes: 12580819,
  apk_version_code: 23,
  ...over,
});

describe('checkApkUpdate', () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.isNative.mockReturnValue(true);
    mocks.getInfo.mockResolvedValue({ build: '21' });
    mocks.origin = 'https://jarvis.example.com';
  });

  it('reports an update when the published build is newer than the running one', async () => {
    mocks.get.mockResolvedValue({ data: remote() });
    const status = await checkApkUpdate();
    expect(status.updateAvailable).toBe(true);
    expect(status.apkVersionCode).toBe(23);
    expect(status.nativeBuildNumber).toBe(21);
    expect(status.sizeBytes).toBe(12580819);
  });

  it('resolves a server-relative apk_url against the server origin', async () => {
    mocks.get.mockResolvedValue({ data: remote() });
    const status = await checkApkUpdate();
    expect(status.apkUrl).toBe('https://jarvis.example.com/api/app-updates/app-debug.apk');
  });

  it('upgrades the legacy plain-http host to https', async () => {
    mocks.get.mockResolvedValue({
      data: remote({ apk_url: 'http://jarvis.sumemail.com/api/app-updates/app-debug.apk' }),
    });
    const status = await checkApkUpdate();
    expect(status.apkUrl).toBe('https://jarvis.sumemail.com/api/app-updates/app-debug.apk');
  });

  it('does not report an update when the published build is the one already installed', async () => {
    mocks.get.mockResolvedValue({ data: remote({ apk_version_code: 21 }) });
    const status = await checkApkUpdate();
    expect(status.updateAvailable).toBe(false);
    expect(status.indeterminate).toBe(false);
  });

  it('does not report an update when the published build is older than the running one', async () => {
    mocks.get.mockResolvedValue({ data: remote({ apk_version_code: 19 }) });
    const status = await checkApkUpdate();
    expect(status.updateAvailable).toBe(false);
  });

  it('stays quiet when no APK is published', async () => {
    mocks.get.mockResolvedValue({ data: remote({ apk_available: false, apk_url: null }) });
    const status = await checkApkUpdate();
    expect(status.updateAvailable).toBe(false);
    expect(status.apkUrl).toBeNull();
  });

  // Fail fast: an APK we cannot version-compare must not read as "up to date".
  it('flags indeterminate when an APK exists but carries no version code', async () => {
    mocks.get.mockResolvedValue({ data: remote({ apk_version_code: undefined }) });
    const status = await checkApkUpdate();
    expect(status.updateAvailable).toBe(false);
    expect(status.indeterminate).toBe(true);
  });

  it('never treats a zero version code as newer than a real build', async () => {
    mocks.get.mockResolvedValue({ data: remote({ apk_version_code: 0 }) });
    const status = await checkApkUpdate();
    expect(status.updateAvailable).toBe(false);
    expect(status.indeterminate).toBe(false);
  });

  it('reports a probe failure instead of silently claiming the app is current', async () => {
    mocks.get.mockRejectedValue(new Error('network down'));
    const status = await checkApkUpdate();
    expect(status.updateAvailable).toBe(false);
    expect(status.error).toBeTruthy();
  });

  // A browser is not running an APK, so there is no honest build number to
  // quote — and "build 1" was previously being displayed as if it were real.
  it('reports no running build number in a browser rather than inventing one', async () => {
    mocks.isNative.mockReturnValue(false);
    mocks.get.mockResolvedValue({ data: remote() });
    const status = await checkApkUpdate();
    expect(status.nativeBuildNumber).toBeUndefined();
  });

  it('still surfaces a published build to a browser viewer', async () => {
    mocks.isNative.mockReturnValue(false);
    mocks.get.mockResolvedValue({ data: remote() });
    const status = await checkApkUpdate();
    expect(status.updateAvailable).toBe(true);
  });

  it('reports no running build number when the shell cannot be queried', async () => {
    mocks.getInfo.mockRejectedValue(new Error('no native shell'));
    mocks.get.mockResolvedValue({ data: remote() });
    const status = await checkApkUpdate();
    expect(status.nativeBuildNumber).toBeUndefined();
  });

  it('queries the app-updates version endpoint', async () => {
    mocks.get.mockResolvedValue({ data: remote() });
    await checkApkUpdate();
    expect(mocks.get).toHaveBeenCalledWith(
      'https://jarvis.example.com/api/app-updates/version',
      expect.objectContaining({ timeout: expect.any(Number) }),
    );
  });
});
