import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  autoUpdateApk,
  installStagedApk,
  setApkUpdateBusyCheck,
  __resetApkInstallPlugin,
  __resetAutoApk,
} from '../lib/appUpdater';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  getInfo: vi.fn(),
  canInstall: vi.fn(),
  downloadApk: vi.fn(),
  installDownloadedApk: vi.fn(),
  selfUpdateSupported: vi.fn(),
}));

vi.mock('axios', () => ({ default: { get: mocks.get } }));
vi.mock('../lib/serverUrl', () => ({ getServerOrigin: () => 'https://jarvis.example.com' }));
vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: () => true, getPlatform: () => 'android' },
  registerPlugin: () => ({
    canInstall: mocks.canInstall,
    downloadApk: mocks.downloadApk,
    installDownloadedApk: mocks.installDownloadedApk,
    selfUpdateSupported: mocks.selfUpdateSupported,
    addListener: () => Promise.resolve({ remove: () => Promise.resolve() }),
  }),
}));
vi.mock('@capacitor/app', () => ({ App: { getInfo: mocks.getInfo, addListener: vi.fn() } }));
vi.mock('react-hot-toast', () => {
  const t = () => {};
  return { default: Object.assign(t, { loading: t, success: t, error: t }) };
});

const published = (code: number) => ({
  data: {
    version: '1.6.0',
    apk_available: true,
    apk_url: '/api/app-updates/app.apk',
    apk_version_code: code,
    apk_sha256: 'a'.repeat(64),
  },
});

describe('automatic APK update', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    __resetApkInstallPlugin();
    __resetAutoApk();
    mocks.getInfo.mockResolvedValue({ build: '600' });
    mocks.canInstall.mockResolvedValue({ allowed: true });
    mocks.downloadApk.mockResolvedValue({ path: 'x', sha256: 'a'.repeat(64), bytes: 1 });
    mocks.installDownloadedApk.mockResolvedValue({ message: 'ok' });
    mocks.selfUpdateSupported.mockResolvedValue({ supported: true });
  });

  it('downloads a newer published APK and installs it once the app is in the background', async () => {
    mocks.get.mockResolvedValue(published(611));
    await autoUpdateApk(1000);
    expect(mocks.downloadApk).toHaveBeenCalledWith({
      url: 'https://jarvis.example.com/api/app-updates/app.apk',
      sha256: 'a'.repeat(64),
    });
    expect(mocks.installDownloadedApk).not.toHaveBeenCalled(); // still in front

    await installStagedApk(); // the app went to the background
    expect(mocks.installDownloadedApk).toHaveBeenCalledTimes(1);
  });

  it('does nothing when the installed build is current', async () => {
    mocks.get.mockResolvedValue(published(600));
    await autoUpdateApk(1000);
    expect(mocks.downloadApk).not.toHaveBeenCalled();
  });

  it('does not retry the same version within six hours', async () => {
    mocks.get.mockResolvedValue(published(611));
    mocks.downloadApk.mockRejectedValueOnce(new Error('network'));
    await autoUpdateApk(1000);
    await autoUpdateApk(2000);
    expect(mocks.downloadApk).toHaveBeenCalledTimes(1);
    await autoUpdateApk(1000 + 6 * 60 * 60 * 1000 + 1);
    expect(mocks.downloadApk).toHaveBeenCalledTimes(2);
  });

  it('waits while a call is live', async () => {
    mocks.get.mockResolvedValue(published(611));
    await autoUpdateApk(1000);
    let inCall = true;
    setApkUpdateBusyCheck(() => inCall);
    await installStagedApk();
    expect(mocks.installDownloadedApk).not.toHaveBeenCalled();
    inCall = false;
    await installStagedApk();
    expect(mocks.installDownloadedApk).toHaveBeenCalledTimes(1);
  });

  it('leaves it to Settings when installing apps is not allowed', async () => {
    mocks.get.mockResolvedValue(published(611));
    mocks.canInstall.mockResolvedValue({ allowed: false });
    await autoUpdateApk(1000);
    expect(mocks.downloadApk).not.toHaveBeenCalled();
  });

  it('leaves an older APK (no self-update, guard would flag the installer) to Settings', async () => {
    mocks.get.mockResolvedValue(published(611));
    mocks.selfUpdateSupported.mockRejectedValue(new Error('not implemented'));
    await autoUpdateApk(1000);
    expect(mocks.downloadApk).not.toHaveBeenCalled();
  });
});
