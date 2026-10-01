import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Settings from '../pages/Settings';
import { renderWithProviders } from './render';

const mocks = vi.hoisted(() => ({
  checkApkUpdate: vi.fn(),
  checkForAppUpdates: vi.fn(),
  downloadAndInstallApk: vi.fn(),
  downloadApkWithProgress: vi.fn(),
  installVerifiedApk: vi.fn(),
  getApkInstallPermission: vi.fn(),
  hasVerifiedInstallFlow: vi.fn(),
  openApkInstallSettings: vi.fn(),
  getRunningVersion: vi.fn(),
}));

vi.mock('../lib/appUpdater', () => ({
  checkApkUpdate: mocks.checkApkUpdate,
  checkForAppUpdates: mocks.checkForAppUpdates,
  downloadAndInstallApk: mocks.downloadAndInstallApk,
  downloadApkWithProgress: mocks.downloadApkWithProgress,
  installVerifiedApk: mocks.installVerifiedApk,
  getApkInstallPermission: mocks.getApkInstallPermission,
  hasVerifiedInstallFlow: mocks.hasVerifiedInstallFlow,
  openApkInstallSettings: mocks.openApkInstallSettings,
  getRunningVersion: mocks.getRunningVersion,
}));

const SHA = 'a'.repeat(64);

const status = (over: Record<string, unknown> = {}) => ({
  updateAvailable: false,
  indeterminate: false,
  apkUrl: null,
  nativeBuildNumber: 21,
  ...over,
});

const pending = status({
  updateAvailable: true,
  apkUrl: 'https://jarvis.example.com/api/app-updates/app-debug.apk',
  apkVersionCode: 23,
  sizeBytes: 12580819,
});

describe('Settings APK update notice', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getRunningVersion.mockResolvedValue({ version: '1.5.0', gitSha: 'abc1234' });
    mocks.checkApkUpdate.mockResolvedValue(status());
    mocks.getApkInstallPermission.mockResolvedValue({ allowed: true, known: true });
    mocks.hasVerifiedInstallFlow.mockResolvedValue(false);
    mocks.openApkInstallSettings.mockResolvedValue(true);
    mocks.checkForAppUpdates.mockResolvedValue({
      hasUpdate: false,
      isDownloading: false,
      currentGitSha: 'abc1234',
      remoteGitSha: 'abc1234',
      remoteVersion: '1.5.0',
      apkUpdateAvailable: false,
      apkUrl: null,
    });
  });

  // The bug this covers: the notice used to be state written only by "Check Now",
  // so nothing was shown until the user pressed it.
  it('notifies about a pending APK on arrival, without pressing Check Now', async () => {
    mocks.checkApkUpdate.mockResolvedValue(pending);
    renderWithProviders(<Settings />);
    expect(await screen.findByTestId('apk-update-notice')).toBeInTheDocument();
    expect(screen.getByText(/New app version available/i)).toBeInTheDocument();
    expect(screen.getByText(/Install build 23/i)).toBeInTheDocument();
    expect(screen.getByText(/You are on build 21/i)).toBeInTheDocument();
    expect(mocks.checkForAppUpdates).not.toHaveBeenCalled();
  });

  it('offers a download link pointing at the published APK', async () => {
    mocks.checkApkUpdate.mockResolvedValue(pending);
    renderWithProviders(<Settings />);
    const link = await screen.findByRole('link', { name: /download link/i });
    expect(link).toHaveAttribute('href', 'https://jarvis.example.com/api/app-updates/app-debug.apk');
    expect(link).toHaveAttribute('download', 'jarvis-os.apk');
  });

  it('installs through the native path when the install button is used', async () => {
    mocks.checkApkUpdate.mockResolvedValue(pending);
    renderWithProviders(<Settings />);
    await screen.findByTestId('apk-update-notice');
    await userEvent.click(await screen.findByRole('button', { name: /install update/i }, { timeout: 5000 }));

    await waitFor(() =>
      expect(mocks.downloadAndInstallApk).toHaveBeenCalledWith(
        'https://jarvis.example.com/api/app-updates/app-debug.apk',
      ),
    );
  });

  // Claiming "Up to Date" next to a waiting install was the visible contradiction.
  it('does not claim the app is up to date while an APK is pending', async () => {
    mocks.checkApkUpdate.mockResolvedValue(pending);
    renderWithProviders(<Settings />);
    await screen.findByTestId('apk-update-notice');
    expect(screen.getByText('App Update Ready')).toBeInTheDocument();
    expect(screen.queryByText('Up to Date')).not.toBeInTheDocument();
  });

  it('does not invent a running build number when none is known', async () => {
    mocks.checkApkUpdate.mockResolvedValue({ ...pending, nativeBuildNumber: undefined });
    renderWithProviders(<Settings />);
    const notice = await screen.findByTestId('apk-update-notice');
    expect(notice).toHaveTextContent(/Install build 23/i);
    expect(notice).not.toHaveTextContent(/You are on build/i);
  });

  it('shows no notice when the published build is already installed', async () => {
    mocks.checkApkUpdate.mockResolvedValue(status({ apkUrl: 'https://x/apk', apkVersionCode: 21 }));
    renderWithProviders(<Settings />);
    await screen.findByText('Not Checked');
    expect(screen.queryByTestId('apk-update-notice')).not.toBeInTheDocument();
  });

  it('surfaces an unversionable APK as unknown rather than up to date', async () => {
    mocks.checkApkUpdate.mockResolvedValue(
      status({ indeterminate: true, apkUrl: 'https://x/apk' }),
    );
    renderWithProviders(<Settings />);
    expect(await screen.findByTestId('apk-update-unknown')).toBeInTheDocument();
    expect(screen.queryByTestId('apk-update-notice')).not.toBeInTheDocument();
  });

  it('surfaces a failed probe instead of hiding it', async () => {
    mocks.checkApkUpdate.mockResolvedValue(status({ error: 'Could not reach the update server.' }));
    renderWithProviders(<Settings />);
    expect(await screen.findByTestId('apk-update-error')).toHaveTextContent(
      /Could not check for app updates/i,
    );
  });

  it('re-probes for APK detail when Check Now is pressed', async () => {
    renderWithProviders(<Settings />);
    await screen.findByText('Not Checked');
    mocks.checkApkUpdate.mockClear();
    await userEvent.click(screen.getByRole('button', { name: /check now/i }));
    await waitFor(() => expect(mocks.checkApkUpdate).toHaveBeenCalledTimes(1));
  });
});

describe('Settings APK install permission', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getRunningVersion.mockResolvedValue({ version: '1.5.0', gitSha: 'abc1234' });
    mocks.checkApkUpdate.mockResolvedValue(pending);
    mocks.hasVerifiedInstallFlow.mockResolvedValue(false);
    mocks.openApkInstallSettings.mockResolvedValue(true);
    mocks.checkForAppUpdates.mockResolvedValue({
      hasUpdate: false,
      isDownloading: false,
      currentGitSha: 'abc1234',
      remoteGitSha: 'abc1234',
      remoteVersion: '1.5.0',
      apkUpdateAvailable: false,
      apkUrl: null,
    });
  });

  // The permission can only be granted in system Settings, so offering a
  // download first just bounced the user out mid-flow and back again.
  it('offers the one-time grant instead of downloading when not yet permitted', async () => {
    mocks.getApkInstallPermission.mockResolvedValue({ allowed: false, known: true });
    renderWithProviders(<Settings />);

    await screen.findByTestId('apk-update-notice');
    expect(await screen.findByRole('button', { name: /allow updates/i }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /install update/i })).not.toBeInTheDocument();
    expect(screen.getByText(/one-time setup/i)).toBeInTheDocument();
  });

  it('installs directly when the permission is already granted', async () => {
    mocks.getApkInstallPermission.mockResolvedValue({ allowed: true, known: true });
    renderWithProviders(<Settings />);

    await screen.findByTestId('apk-update-notice');
    await userEvent.click(await screen.findByRole('button', { name: /install update/i }, { timeout: 5000 }));

    await waitFor(() =>
      expect(mocks.downloadAndInstallApk).toHaveBeenCalledWith(
        'https://jarvis.example.com/api/app-updates/app-debug.apk',
      ),
    );
  });

  it('opens install settings from the grant button', async () => {
    mocks.getApkInstallPermission.mockResolvedValue({ allowed: false, known: true });
    renderWithProviders(<Settings />);
    await screen.findByTestId('apk-update-notice');
    await userEvent.click(await screen.findByRole('button', { name: /allow updates/i }, { timeout: 5000 }));

    await waitFor(() => expect(mocks.openApkInstallSettings).toHaveBeenCalled());
    expect(mocks.downloadAndInstallApk).not.toHaveBeenCalled();
  });

  // No plugin means no in-app install; offering a button that cannot work is
  // the same trap as the old silent browser fallback.
  it('offers only the download link when the install path is unavailable', async () => {
    mocks.getApkInstallPermission.mockResolvedValue({ allowed: false, known: false });
    renderWithProviders(<Settings />);

    expect(await screen.findByRole('link', { name: /download link/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /install update/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /allow updates/i })).not.toBeInTheDocument();
  });
});

describe('Settings APK verified download flow', () => {
  const verified = status({
    updateAvailable: true,
    apkUrl: 'https://jarvis.example.com/api/app-updates/jarvis-os.apk',
    apkVersionCode: 23,
    apkSha256: SHA,
    sizeBytes: 12580819,
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getRunningVersion.mockResolvedValue({ version: '1.5.0', gitSha: 'abc1234' });
    mocks.checkApkUpdate.mockResolvedValue(verified);
    mocks.getApkInstallPermission.mockResolvedValue({ allowed: true, known: true });
    mocks.hasVerifiedInstallFlow.mockResolvedValue(true);
    mocks.downloadApkWithProgress.mockResolvedValue({ path: '/p', sha256: SHA, bytes: 12580819 });
    mocks.installVerifiedApk.mockResolvedValue(undefined);
    mocks.checkForAppUpdates.mockResolvedValue({
      hasUpdate: false,
      isDownloading: false,
      currentGitSha: 'abc1234',
      remoteGitSha: 'abc1234',
      remoteVersion: '1.5.0',
      apkUpdateAvailable: false,
      apkUrl: null,
    });
  });

  // The whole point: tap Download in the app, never hand off to a browser.
  it('downloads in-app with the published digest rather than opening a link', async () => {
    renderWithProviders(<Settings />);
    await userEvent.click(await screen.findByTestId('apk-download-button'));

    await waitFor(() =>
      expect(mocks.downloadApkWithProgress).toHaveBeenCalledWith(
        'https://jarvis.example.com/api/app-updates/jarvis-os.apk',
        SHA,
        expect.any(Function),
      ),
    );
    expect(screen.queryByRole('link', { name: /download link/i })).not.toBeInTheDocument();
  });

  it('shows progress while downloading and then offers Install', async () => {
    // The progress state only exists while the download promise is pending, so
    // it has to be held open deliberately or the test can never observe it.
    let settle: (() => void) | null = null;
    mocks.downloadApkWithProgress.mockImplementation((_u, _s, onProgress) => {
      onProgress({ received: 6291456, total: 12580819, percent: 50, done: false });
      return new Promise((resolve) => {
        settle = () => resolve({ path: '/p', sha256: SHA, bytes: 12580819 });
      });
    });

    renderWithProviders(<Settings />);
    await userEvent.click(await screen.findByTestId('apk-download-button'));

    const bar = await screen.findByTestId('apk-download-bar');
    expect(bar).toHaveStyle({ width: '50%' });
    expect(screen.getByText('50%')).toBeInTheDocument();
    // Still downloading, so no install action yet.
    expect(screen.queryByTestId('apk-install-button')).not.toBeInTheDocument();

    await act(async () => {
      settle!();
    });

    expect(await screen.findByTestId('apk-install-button')).toBeInTheDocument();
    expect(screen.getByText(/checksum verified/i)).toBeInTheDocument();
  });

  it('installs the verified file through the system installer', async () => {
    renderWithProviders(<Settings />);
    await userEvent.click(await screen.findByTestId('apk-download-button'));
    await userEvent.click(await screen.findByTestId('apk-install-button'));
    await waitFor(() => expect(mocks.installVerifiedApk).toHaveBeenCalled());
  });

  // A digest mismatch must never leave the file installable.
  it('surfaces a checksum failure and stays out of the install state', async () => {
    mocks.downloadApkWithProgress.mockRejectedValue(
      new Error('Checksum mismatch: expected ' + SHA + ' but got ' + 'b'.repeat(64)),
    );
    renderWithProviders(<Settings />);
    await userEvent.click(await screen.findByTestId('apk-download-button'));

    expect(await screen.findByTestId('apk-download-error')).toHaveTextContent(/checksum mismatch/i);
    expect(screen.queryByTestId('apk-install-button')).not.toBeInTheDocument();
    // And it can be retried.
    expect(await screen.findByTestId('apk-download-button')).toBeInTheDocument();
  });

  // The web bundle can be newer than the installed APK, so the verified flow
  // must be feature-detected rather than assumed.
  it('falls back to the link when this build cannot verify downloads', async () => {
    mocks.hasVerifiedInstallFlow.mockResolvedValue(false);
    renderWithProviders(<Settings />);
    expect(await screen.findByRole('link', { name: /download link/i })).toBeInTheDocument();
    expect(screen.queryByTestId('apk-download-button')).not.toBeInTheDocument();
  });

  it('does not offer a verified install when the server published no digest', async () => {
    mocks.checkApkUpdate.mockResolvedValue({ ...verified, apkSha256: undefined });
    renderWithProviders(<Settings />);
    await screen.findByTestId('apk-update-notice');
    expect(await screen.findByRole('button', { name: /install update/i }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId('apk-download-button')).not.toBeInTheDocument();
  });

  it('asks for the one-time grant before downloading', async () => {
    mocks.getApkInstallPermission.mockResolvedValue({ allowed: false, known: true });
    renderWithProviders(<Settings />);
    await screen.findByTestId('apk-update-notice');
    expect(await screen.findByRole('button', { name: /allow updates/i }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId('apk-download-button')).not.toBeInTheDocument();
  });
});
