import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Settings from '../pages/Settings';
import { renderWithProviders } from './render';

const mocks = vi.hoisted(() => ({
  checkApkUpdate: vi.fn(),
  checkForAppUpdates: vi.fn(),
  downloadAndInstallApk: vi.fn(),
  getRunningVersion: vi.fn(),
}));

vi.mock('../lib/appUpdater', () => ({
  checkApkUpdate: mocks.checkApkUpdate,
  checkForAppUpdates: mocks.checkForAppUpdates,
  downloadAndInstallApk: mocks.downloadAndInstallApk,
  getRunningVersion: mocks.getRunningVersion,
}));

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
    expect(link).toHaveAttribute('download', 'app-debug.apk');
  });

  it('installs through the native path when the install button is used', async () => {
    mocks.checkApkUpdate.mockResolvedValue(pending);
    renderWithProviders(<Settings />);
    await userEvent.click(await screen.findByRole('button', { name: /install update/i }));
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
