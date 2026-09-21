import { Capacitor } from '@capacitor/core';
import { CapacitorUpdater } from '@capgo/capacitor-updater';
import { App } from '@capacitor/app';
import { getServerOrigin } from './serverUrl';
import axios from 'axios';
import toast from 'react-hot-toast';

export interface AppVersionInfo {
  version: string;
  git_sha: string;
  build_timestamp?: string;
  release_notes?: string;
  bundle_url?: string;
  bundle_available?: boolean;
  apk_available?: boolean;
  apk_url?: string | null;
  apk_size_bytes?: number;
  apk_version_code?: number;
}

export interface CheckUpdateResult {
  hasUpdate: boolean;
  isDownloading: boolean;
  currentGitSha: string;
  remoteGitSha: string;
  remoteVersion: string;
  releaseNotes?: string;
  apkUpdateAvailable: boolean;
  apkUrl?: string | null;
}

let isInitialized = false;
let currentRuntimeSha = 'unknown';

/**
 * Get current runtime web SHA from version.json bundled with the web app
 */
export async function getRunningVersion(): Promise<{ version: string; gitSha: string }> {
  try {
    const res = await fetch('/version.json?t=' + Date.now(), { cache: 'no-cache' });
    if (res.ok) {
      const data = await res.json();
      currentRuntimeSha = data.gitSha || data.git_sha || 'unknown';
      return { version: data.version || '1.1.2', gitSha: currentRuntimeSha };
    }
  } catch {
    // ignore
  }
  return { version: '1.1.2', gitSha: currentRuntimeSha };
}

/**
 * Call on app startup to notify CapGo plugin the bundle loaded successfully,
 * preventing accidental rollback, and triggers a background check for updates.
 */
export async function initAppUpdater(): Promise<void> {
  if (isInitialized) return;
  isInitialized = true;

  await getRunningVersion();

  if (Capacitor.isNativePlatform()) {
    try {
      await CapacitorUpdater.notifyAppReady();
      console.log('[AppUpdater] CapGo updater notified: app ready, bundle verified');
    } catch (err) {
      console.warn('[AppUpdater] Failed to notify app ready:', err);
    }

    // Schedule background check 3 seconds after launch to keep startup lightning fast
    setTimeout(() => {
      void checkForAppUpdates({ silent: true });
    }, 3000);
  }
}

/**
 * Check for updates against current server endpoint (local LAN or external domain)
 */
export async function checkForAppUpdates(options: { silent?: boolean } = {}): Promise<CheckUpdateResult> {
  const origin = getServerOrigin();
  const updateUrl = `${origin}/api/app-updates/version`;

  try {
    const resp = await axios.get<AppVersionInfo>(updateUrl, { timeout: 7000 });
    const remote = resp.data;

    await getRunningVersion();

    let nativeBuildNumber = 1;
    if (Capacitor.isNativePlatform()) {
      try {
        const appInfo = await App.getInfo();
        nativeBuildNumber = parseInt(appInfo.build, 10) || 1;
      } catch {
        // fallback
      }
    }

    const apkUpdateAvailable = Boolean(
      remote.apk_available &&
      remote.apk_url &&
      remote.apk_version_code &&
      remote.apk_version_code > nativeBuildNumber
    );

    const hasWebUpdate = Boolean(
      remote.bundle_available &&
      remote.git_sha &&
      remote.git_sha !== 'unknown' &&
      currentRuntimeSha !== 'unknown' &&
      !currentRuntimeSha.startsWith(remote.git_sha) &&
      !remote.git_sha.startsWith(currentRuntimeSha)
    );

    const effectiveBundleUrl = remote.bundle_url
      ? (remote.bundle_url.startsWith('http')
          ? remote.bundle_url.replace(/^http:\/\/jarvis\.sumemail\.com/, 'https://jarvis.sumemail.com')
          : `${origin}${remote.bundle_url.startsWith('/') ? '' : '/'}${remote.bundle_url}`)
      : '';

    const effectiveApkUrl = remote.apk_url
      ? (remote.apk_url.startsWith('http')
          ? remote.apk_url.replace(/^http:\/\/jarvis\.sumemail\.com/, 'https://jarvis.sumemail.com')
          : `${origin}${remote.apk_url.startsWith('/') ? '' : '/'}${remote.apk_url}`)
      : null;

    if (hasWebUpdate && effectiveBundleUrl && Capacitor.isNativePlatform()) {
      if (!options.silent) {
        toast.loading('Downloading update...', { id: 'app-update' });
      }

      console.log(`[AppUpdater] Downloading OTA bundle from ${effectiveBundleUrl} (version: ${remote.git_sha})`);
      const bundle = await CapacitorUpdater.download({
        url: effectiveBundleUrl,
        version: remote.git_sha,
      });

      await CapacitorUpdater.set(bundle);
      console.log('[AppUpdater] Update staged successfully!');

      if (options.silent) {
        toast(
          'Jarvis OS update ready! Restarting on next launch or tap to reload.',
          {
            icon: '🚀',
            duration: 8000,
            id: 'app-update-ready',
          }
        );
      } else {
        toast.success('Update ready! Restarting...', { id: 'app-update' });
        setTimeout(() => {
          void CapacitorUpdater.reload();
        }, 1200);
      }

      return {
        hasUpdate: true,
        isDownloading: false,
        currentGitSha: currentRuntimeSha,
        remoteGitSha: remote.git_sha,
        remoteVersion: remote.version,
        releaseNotes: remote.release_notes,
        apkUpdateAvailable,
        apkUrl: effectiveApkUrl,
      };
    }

    if (!options.silent) {
      if (apkUpdateAvailable) {
        toast('New native APK build available.', { icon: '📦' });
      } else {
        toast.success('Jarvis OS is up to date!', { id: 'app-update' });
      }
    }

    return {
      hasUpdate: hasWebUpdate,
      isDownloading: false,
      currentGitSha: currentRuntimeSha,
      remoteGitSha: remote.git_sha,
      remoteVersion: remote.version,
      releaseNotes: remote.release_notes,
      apkUpdateAvailable,
      apkUrl: effectiveApkUrl,
    };
  } catch (err) {
    if (!options.silent) {
      console.error('[AppUpdater] Failed to check for updates:', err);
      toast.error('Unable to reach update server.', { id: 'app-update' });
    }
    return {
      hasUpdate: false,
      isDownloading: false,
      currentGitSha: currentRuntimeSha,
      remoteGitSha: 'unknown',
      remoteVersion: '1.1.2',
      apkUpdateAvailable: false,
    };
  }
}

/**
 * Download and launch Android system package installer for APK updates
 */
export function downloadAndInstallApk(apkUrl: string): void {
  if (!apkUrl) return;
  toast.loading('Opening APK download...', { duration: 3000 });
  window.open(apkUrl, '_system');
}
