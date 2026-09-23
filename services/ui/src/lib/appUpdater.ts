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
  /** Set when an update was found but deliberately not applied (see reason). */
  blockedReason?: string;
}

let isInitialized = false;
let currentRuntimeSha: string = typeof __BUILD_SHA__ === 'string' ? __BUILD_SHA__ : 'unknown';

// Native build last known to CapGo. Bumping versionCode (new APK) must drop any
// OTA web bundle that predates it — otherwise CapGo keeps serving stale JS over
// the freshly installed APK assets (sensor UI, plugins, widget auth, etc.).
const LAST_NATIVE_BUILD_KEY = 'jarvis_last_native_build';

// --- OTA loop guard --------------------------------------------------------
// `CapacitorUpdater.set()` destroys the JS context and reloads the app
// immediately — it does not merely stage a bundle for later. So an update check
// that always reports "newer" becomes an endless download -> reload cycle. That
// is exactly what happens when the SHA the server advertises is not the SHA
// baked into the bundle it actually serves.
//
// We record the version we are about to install; on the next boot we check
// whether we are really running it. If not, the server's metadata does not
// describe its own bundle, so we blacklist that version instead of chasing it
// forever.
const PENDING_SHA_KEY = 'jarvis_ota_pending_sha';
const REJECTED_SHAS_KEY = 'jarvis_ota_rejected_shas';
const MAX_REJECTED_TRACKED = 10;

function shaMatches(a: string, b: string): boolean {
  if (!a || !b || a === 'unknown' || b === 'unknown') return false;
  return a.startsWith(b) || b.startsWith(a);
}

function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // storage unavailable — the guard degrades to a no-op rather than throwing
  }
}

function getRejectedShas(): string[] {
  const raw = readLocal(REJECTED_SHAS_KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

function rejectSha(sha: string): void {
  if (!sha || sha === 'unknown') return;
  const list = getRejectedShas().filter((s) => s !== sha);
  list.push(sha);
  writeLocal(REJECTED_SHAS_KEY, JSON.stringify(list.slice(-MAX_REJECTED_TRACKED)));
}

function isRejected(sha: string): boolean {
  return getRejectedShas().some((s) => shaMatches(s, sha));
}

/**
 * Compare the bundle we last tried to install against what is actually running.
 * A mismatch means the server advertised a version its bundle does not contain,
 * so we blacklist it and stop reload-looping on it.
 */
function reconcilePendingUpdate(): void {
  const pending = readLocal(PENDING_SHA_KEY);
  if (!pending) return;
  writeLocal(PENDING_SHA_KEY, null);

  if (shaMatches(currentRuntimeSha, pending)) {
    console.log(`[AppUpdater] OTA bundle ${pending} applied successfully.`);
    return;
  }

  rejectSha(pending);
  console.warn(
    `[AppUpdater] Server advertised OTA version "${pending}" but the bundle it served ` +
    `reports "${currentRuntimeSha}". Ignoring that version to avoid a reload loop. ` +
    `Fix on the server: republish so /api/app-updates/version matches bundle.zip's version.json.`,
  );
}

/** Forget blacklisted versions so a manual check can retry from scratch. */
export function resetUpdateGuard(): void {
  writeLocal(REJECTED_SHAS_KEY, null);
  writeLocal(PENDING_SHA_KEY, null);
}

/**
 * Get current runtime web SHA from version.json bundled with the web app.
 * Falls back to the __BUILD_SHA__ constant embedded at build time, so native
 * builds always know their own SHA even if /version.json is unavailable.
 */
export async function getRunningVersion(): Promise<{ version: string; gitSha: string }> {
  try {
    const res = await fetch('/version.json?t=' + Date.now(), { cache: 'no-cache' });
    if (res.ok) {
      const data = await res.json();
      currentRuntimeSha = data.gitSha || data.git_sha || currentRuntimeSha;
      return { version: data.version || '1.2.0', gitSha: currentRuntimeSha };
    }
  } catch {
    // ignore — fall back to embedded build SHA
  }
  return { version: '1.2.0', gitSha: currentRuntimeSha };
}

/**
 * Call on app startup to notify CapGo plugin the bundle loaded successfully,
 * preventing accidental rollback, and triggers a background check for updates.
 */
export async function initAppUpdater(): Promise<void> {
  if (isInitialized) return;
  isInitialized = true;

  await getRunningVersion();
  reconcilePendingUpdate();

  if (Capacitor.isNativePlatform()) {
    let nativeBuildNumber = 1;
    try {
      const appInfo = await App.getInfo();
      nativeBuildNumber = parseInt(appInfo.build, 10) || 1;
    } catch {
      // keep default
    }

    // New APK install/upgrade: discard any OTA bundle from a previous native
    // build so the APK's bundled assets are what the WebView loads.
    // MUST record the build (and notify ready) BEFORE reset() — reset destroys
    // this JS context and reloads the app; nothing after it runs.
    const lastBuild = parseInt(readLocal(LAST_NATIVE_BUILD_KEY) || '0', 10) || 0;
    const needsNativeReset = nativeBuildNumber > lastBuild;
    if (needsNativeReset || !readLocal(LAST_NATIVE_BUILD_KEY)) {
      writeLocal(LAST_NATIVE_BUILD_KEY, String(nativeBuildNumber));
    }

    try {
      await CapacitorUpdater.notifyAppReady();
      console.log('[AppUpdater] CapGo updater notified: app ready, bundle verified');
    } catch (err) {
      console.warn('[AppUpdater] Failed to notify app ready:', err);
    }

    if (needsNativeReset) {
      try {
        console.log(
          `[AppUpdater] Native build ${nativeBuildNumber} > ${lastBuild} — reset CapGo to bundled assets`,
        );
        await CapacitorUpdater.reset({ toLastSuccessful: false });
      } catch (err) {
        console.warn('[AppUpdater] CapGo reset after native upgrade failed:', err);
      }
    }

    // Schedule background check 3 seconds after launch to keep startup lightning fast
    setTimeout(() => {
      void checkForAppUpdates({ silent: true });
    }, 3000);

    // Periodic silent re-check (every 6 hours) for long-lived sessions
    setInterval(() => {
      void checkForAppUpdates({ silent: true });
    }, 6 * 60 * 60 * 1000);
  }
}

/**
 * Check for updates against current server endpoint (local LAN or external domain).
 *
 * Silent (background) checks stage the bundle with `next()`, which activates on
 * the next cold start. Only an explicit user-initiated check applies it with
 * `set()`, which reloads the app there and then.
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
      !shaMatches(currentRuntimeSha, remote.git_sha)
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

    const baseResult: CheckUpdateResult = {
      hasUpdate: hasWebUpdate,
      isDownloading: false,
      currentGitSha: currentRuntimeSha,
      remoteGitSha: remote.git_sha,
      remoteVersion: remote.version,
      releaseNotes: remote.release_notes,
      apkUpdateAvailable,
      apkUrl: effectiveApkUrl,
    };

    if (hasWebUpdate && effectiveBundleUrl && Capacitor.isNativePlatform()) {
      // A version we already installed once without the runtime SHA changing is
      // mislabelled on the server. Applying it again would just reload forever.
      if (isRejected(remote.git_sha)) {
        const reason =
          `Server version "${remote.git_sha}" does not match the bundle it serves ` +
          `(still running "${currentRuntimeSha}" after installing it). Update skipped.`;
        console.warn(`[AppUpdater] ${reason}`);
        if (!options.silent) {
          toast.error('Update on the server is mislabelled — skipping to avoid a restart loop.', {
            id: 'app-update',
            duration: 8000,
          });
        }
        return { ...baseResult, blockedReason: reason };
      }

      if (!options.silent) {
        toast.loading('Downloading update...', { id: 'app-update' });
      }

      console.log(`[AppUpdater] Downloading OTA bundle from ${effectiveBundleUrl} (version: ${remote.git_sha})`);
      const bundle = await CapacitorUpdater.download({
        url: effectiveBundleUrl,
        version: remote.git_sha,
      });

      // Record the attempt BEFORE activating: set()/next() may tear down this
      // JS context, and the next boot needs to know what we expected to get.
      writeLocal(PENDING_SHA_KEY, remote.git_sha);

      if (options.silent) {
        // Background check: stage only. `next()` activates on the next cold
        // start instead of yanking the app out from under the user.
        await CapacitorUpdater.next({ id: bundle.id });
        console.log('[AppUpdater] Update staged — will activate on next app launch.');
        toast('Jarvis OS update ready — it will apply next time you open the app.', {
          icon: '🚀',
          duration: 6000,
          id: 'app-update-ready',
        });
        return { ...baseResult, hasUpdate: true };
      }

      toast.success('Update ready! Restarting...', { id: 'app-update' });
      // NOTE: set() destroys the JS context and reloads immediately; nothing
      // after this line is guaranteed to run.
      await CapacitorUpdater.set({ id: bundle.id });
      return { ...baseResult, hasUpdate: true };
    }

    if (!options.silent) {
      if (apkUpdateAvailable) {
        toast('New native APK build available.', { icon: '📦' });
      } else {
        toast.success('Jarvis OS is up to date!', { id: 'app-update' });
      }
    }

    return baseResult;
  } catch (err) {
    if (!options.silent) {
      console.error('[AppUpdater] Failed to check for updates:', err);
      toast.error('Unable to reach update server.', { id: 'app-update' });
    }
    // A failed download must not leave a pending marker behind, or the next
    // boot would wrongly blacklist a version we never actually installed.
    writeLocal(PENDING_SHA_KEY, null);
    return {
      hasUpdate: false,
      isDownloading: false,
      currentGitSha: currentRuntimeSha,
      remoteGitSha: 'unknown',
      remoteVersion: '1.2.0',
      apkUpdateAvailable: false,
    };
  }
}

/**
 * Download and launch Android system package installer for APK updates.
 * Uses the native ApkInstall plugin (self-signed, no Play Store) when available;
 * falls back to opening the URL in the system browser.
 */
export async function downloadAndInstallApk(apkUrl: string): Promise<void> {
  if (!apkUrl) return;
  if (Capacitor.isNativePlatform()) {
    try {
      const { registerPlugin } = await import('@capacitor/core');
      const ApkInstall = registerPlugin<{
        canInstall(): Promise<{ allowed: boolean }>;
        openInstallSettings(): Promise<void>;
        installApk(opts: { url: string }): Promise<{ message: string }>;
      }>('ApkInstall');
      const can = await ApkInstall.canInstall();
      if (!can.allowed) {
        toast('Allow “Install unknown apps” for Jarvis OS to continue.', {
          id: 'apk-install',
          duration: 8000,
        });
        await ApkInstall.openInstallSettings();
        return;
      }
      toast.loading('Downloading APK…', { id: 'apk-install' });
      await ApkInstall.installApk({ url: apkUrl });
      toast.success('Installer opened — tap Install.', { id: 'apk-install', duration: 5000 });
      return;
    } catch (err) {
      console.warn('[AppUpdater] ApkInstall plugin failed, falling back:', err);
    }
  }
  toast.loading('Opening APK download...', { duration: 3000, id: 'apk-install' });
  window.open(apkUrl, '_system');
}
