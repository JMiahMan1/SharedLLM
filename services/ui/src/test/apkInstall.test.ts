import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { downloadAndInstallApk, getApkInstallPermission, openApkInstallSettings } from '../lib/appUpdater';

const mocks = vi.hoisted(() => ({
  isNative: vi.fn(() => true),
  registerPlugin: vi.fn(),
  open: vi.fn(),
  canInstall: vi.fn(),
  openInstallSettings: vi.fn(),
  installApk: vi.fn(),
}));

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: mocks.isNative },
  registerPlugin: mocks.registerPlugin,
}));
vi.mock('react-hot-toast', () => {
  const t = () => {};
  return { default: Object.assign(t, { loading: t, success: t, error: t }) };
});

const plugin = () => ({
  canInstall: mocks.canInstall,
  openInstallSettings: mocks.openInstallSettings,
  installApk: mocks.installApk,
});

describe('APK install path', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.isNative.mockReturnValue(true);
    mocks.registerPlugin.mockReturnValue(plugin());
    mocks.canInstall.mockResolvedValue({ allowed: true });
    mocks.openInstallSettings.mockResolvedValue(undefined);
    mocks.installApk.mockResolvedValue({ message: 'Installer opened' });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('installs in-app when the OS allows it', async () => {
    await downloadAndInstallApk('https://x/app-debug.apk');
    expect(mocks.installApk).toHaveBeenCalledWith({ url: 'https://x/app-debug.apk' });
  });

  // The reported bug: tapping install opened a browser, because the native
  // path threw and the old code fell through to window.open(url, '_system').
  it('never falls back to a browser when the plugin is missing', async () => {
    const open = vi.fn();
    vi.stubGlobal('window', { open, location: { origin: 'https://x' } });
    mocks.registerPlugin.mockImplementation(() => {
      throw new Error('plugin is not implemented on android');
    });

    await downloadAndInstallApk('https://x/app-debug.apk');

    expect(open).not.toHaveBeenCalled();
    expect(mocks.installApk).not.toHaveBeenCalled();
  });

  it('never falls back to a browser when the install itself fails', async () => {
    const open = vi.fn();
    vi.stubGlobal('window', { open, location: { origin: 'https://x' } });
    mocks.installApk.mockRejectedValue(new Error('boom'));

    await downloadAndInstallApk('https://x/app-debug.apk');

    expect(open).not.toHaveBeenCalled();
  });

  it('does not open a browser off-device', async () => {
    const open = vi.fn();
    vi.stubGlobal('window', { open, location: { origin: 'https://x' } });
    mocks.isNative.mockReturnValue(false);

    await downloadAndInstallApk('https://x/app-debug.apk');

    expect(open).not.toHaveBeenCalled();
    expect(mocks.registerPlugin).not.toHaveBeenCalled();
  });

  it('sends the user to install settings rather than downloading when not permitted', async () => {
    mocks.canInstall.mockResolvedValue({ allowed: false });
    await downloadAndInstallApk('https://x/app-debug.apk');
    expect(mocks.openInstallSettings).toHaveBeenCalled();
    expect(mocks.installApk).not.toHaveBeenCalled();
  });

  it('reports the permission as unknown when the plugin is absent', async () => {
    mocks.registerPlugin.mockImplementation(() => {
      throw new Error('nope');
    });
    await expect(getApkInstallPermission()).resolves.toEqual({ allowed: false, known: false });
  });

  it('reports the permission as unknown off-device', async () => {
    mocks.isNative.mockReturnValue(false);
    await expect(getApkInstallPermission()).resolves.toEqual({ allowed: false, known: false });
  });

  it('reports a real denial as known', async () => {
    mocks.canInstall.mockResolvedValue({ allowed: false });
    await expect(getApkInstallPermission()).resolves.toEqual({ allowed: false, known: true });
  });

  it('reports a granted permission as known', async () => {
    await expect(getApkInstallPermission()).resolves.toEqual({ allowed: true, known: true });
  });

  it('reports unknown rather than allowed when the probe throws', async () => {
    mocks.canInstall.mockRejectedValue(new Error('bridge gone'));
    await expect(getApkInstallPermission()).resolves.toEqual({ allowed: false, known: false });
  });

  it('reports failure to open install settings instead of pretending', async () => {
    mocks.openInstallSettings.mockRejectedValue(new Error('no activity'));
    await expect(openApkInstallSettings()).resolves.toBe(false);
  });
});
