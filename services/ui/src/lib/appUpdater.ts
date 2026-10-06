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
  apk_version_name?: string | null;
  /** SHA-256 of the served APK, so the client can verify what it downloads. */
  apk_sha256?: string | null;
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

export interface ApkUpdateStatus {
  /** Server advertises a build strictly newer than the one running. */
  updateAvailable: boolean;
  /**
   * Server has an APK but not enough metadata to compare versions. We refuse
   * to call this "up to date" — the honest answer is that we do not know.
   */
  indeterminate: boolean;
  apkUrl: string | null;
  apkVersionCode?: number;
  /**
   * SHA-256 the server computed over the APK it is serving. Required before we
   * will download-and-install; a missing digest means we cannot authenticate
   * what we fetched, so we fall back to the browser link rather than install
   * an unverified binary.
   */
  apkSha256?: string;
  /**
   * versionCode of the running native build, or `undefined` when it is not
   * knowable — a browser is not running an APK at all, so there is no honest
   * number to quote. Never substitute a placeholder for display.
   */
  nativeBuildNumber?: number;
  sizeBytes?: number;
  /** Populated when the probe itself failed; never silently treated as "current". */
  error?: string;
}

/**
 * Version code of the running native build, or undefined when unknown.
 * Returning a made-up number here would be quoted to the user as fact.
 */
async function getNativeBuildNumber(): Promise<number | undefined> {
  if (!Capacitor.isNativePlatform()) return undefined;
  try {
    const appInfo = await App.getInfo();
    return parseInt(appInfo.build, 10) || undefined;
  } catch {
    return undefined;
  }
}

/**
 * Turn a server-relative update path into an absolute, http->https URL.
 * The published metadata can carry the legacy plain-http host.
 */
function resolveUpdateUrl(origin: string, url: string): string {
  if (url.startsWith('http')) {
    return url.replace(/^http:\/\/jarvis\.sumemail\.com/, 'https://jarvis.sumemail.com');
  }
  return `${origin}${url.startsWith('/') ? '' : '/'}${url}`;
}

/** Decide whether the published APK is newer than the running native build. */
function evaluateApk(remote: AppVersionInfo, nativeBuildNumber: number | undefined, apkUrl: string | null) {
  if (!remote.apk_available || !apkUrl) {
    return { updateAvailable: false, indeterminate: false };
  }
  if (typeof remote.apk_version_code !== 'number') {
    // An APK exists but we cannot compare it. Reporting "up to date" here
    // would be a guess, so report the uncertainty instead.
    return { updateAvailable: false, indeterminate: true };
  }
  if (nativeBuildNumber !== undefined) {
    return { updateAvailable: remote.apk_version_code > nativeBuildNumber, indeterminate: false };
  }
  if (nativeBuildNumber === undefined && Capacitor.isNativePlatform()) {
    // A device whose build we failed to read: claiming either way is a guess,
    // and guessing "up to date" would hide a real update. Say we cannot tell.
    return { updateAvailable: false, indeterminate: true };
  }
  // A browser is not running an APK at all, so there is no installed build to
  // compare against. A published build is still worth offering where it can
  // be installed (an Android browser, or a Chromebook, which runs Android
  // apps); a desktop browser cannot install it, so it is not offered there.
  // The notice omits any "you are on build N" claim, since nativeBuildNumber
  // is undefined.
  return { updateAvailable: browserCanInstallApk(), indeterminate: false };
}

/** Whether this browser's device can install an Android APK: Android itself,
 *  or ChromeOS (Chromebooks run Android apps). */
export function browserCanInstallApk(userAgent: string = typeof navigator === 'undefined' ? '' : navigator.userAgent): boolean {
  return /Android|CrOS/i.test(userAgent);
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

    const nativeBuildNumber = await getNativeBuildNumber();
    const apkUrl = remote.apk_url ? resolveUpdateUrl(origin, remote.apk_url) : null;
    const { updateAvailable: apkUpdateAvailable } = evaluateApk(remote, nativeBuildNumber, apkUrl);

    const hasWebUpdate = Boolean(
      remote.bundle_available &&
      remote.git_sha &&
      remote.git_sha !== 'unknown' &&
      currentRuntimeSha !== 'unknown' &&
      !shaMatches(currentRuntimeSha, remote.git_sha)
    );

    const effectiveBundleUrl = remote.bundle_url ? resolveUpdateUrl(origin, remote.bundle_url) : '';

    const baseResult: CheckUpdateResult = {
      hasUpdate: hasWebUpdate,
      isDownloading: false,
      currentGitSha: currentRuntimeSha,
      remoteGitSha: remote.git_sha,
      remoteVersion: remote.version,
      releaseNotes: remote.release_notes,
      apkUpdateAvailable,
      apkUrl,
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

interface ApkInstallPlugin {
  canInstall(): Promise<{ allowed: boolean }>;
  openInstallSettings(): Promise<void>;
  installApk(opts: { url: string }): Promise<{ message: string }>;
  /** Present only on builds that verify the download; see hasVerifiedFlow(). */
  downloadApk?(opts: { url: string; sha256: string }): Promise<{ path: string; sha256: string; bytes: number }>;
  installDownloadedApk?(): Promise<{ message: string }>;
  isApkDownloaded?(): Promise<{ downloaded: boolean }>;
  clearDownloadedApk?(): Promise<void>;
  addListener?(event: 'downloadProgress', cb: (p: ApkDownloadProgress) => void): Promise<{ remove(): Promise<void> }>;
}

export interface ApkDownloadProgress {
  received: number;
  total: number;
  percent: number;
  done: boolean;
}

export interface ApkDownloadResult {
  path: string;
  sha256: string;
  bytes: number;
}

/** The native installer plugin, or null where it is not registered. */
async function getApkInstallPlugin(): Promise<ApkInstallPlugin | null> {
  if (!Capacitor.isNativePlatform()) return null;
  try {
    const { registerPlugin } = await import('@capacitor/core');
    return registerPlugin<ApkInstallPlugin>('ApkInstall');
  } catch (err) {
    console.warn('[AppUpdater] Could not reach the ApkInstall plugin:', err);
    return null;
  }
}

export interface ApkInstallPermission {
  /** The OS will let this app install an APK. */
  allowed: boolean;
  /**
   * False when we could not find out (a browser, or a build without the
   * plugin). Distinct from `allowed: false`, which is a real "not yet granted".
   */
  known: boolean;
}

/**
 * Whether this device can install an APK yet.
 *
 * Android requires a one-time per-app "Install unknown apps" grant, and it can
 * only be given in system Settings -- there is no way to ask for it inline. So
 * the grant is surfaced *before* the user commits to installing, turning a
 * confusing mid-flow jump into Settings into a single deliberate step.
 */
export async function getApkInstallPermission(): Promise<ApkInstallPermission> {
  const ApkInstall = await getApkInstallPlugin();
  if (!ApkInstall) return { allowed: false, known: false };
  try {
    const can = await ApkInstall.canInstall();
    return { allowed: Boolean(can.allowed), known: true };
  } catch (err) {
    console.warn('[AppUpdater] Install-permission probe failed:', err);
    return { allowed: false, known: false };
  }
}

/**
 * Whether this build can download-and-verify an update itself.
 *
 * The web bundle arrives over OTA, so a brand new UI can land on a device
 * still running the previous APK. Rather than fail, the UI falls back to the
 * old download-and-install path, which does not verify -- so the digest check
 * is honest about when it is actually happening.
 */
export async function hasVerifiedInstallFlow(): Promise<boolean> {
  const ApkInstall = await getApkInstallPlugin();
  return !!ApkInstall && typeof ApkInstall.downloadApk === 'function';
}

/**
 * Download the update, reporting progress, and verify it against the digest
 * the server published. Rejects on a checksum mismatch or a short read, and
 * the file is never left installable in either case.
 */
export async function downloadApkWithProgress(
  apkUrl: string,
  sha256: string,
  onProgress: (p: ApkDownloadProgress) => void,
): Promise<ApkDownloadResult> {
  const ApkInstall = await getApkInstallPlugin();
  if (!ApkInstall || typeof ApkInstall.downloadApk !== 'function') {
    throw new Error('This build cannot download updates in-app.');
  }
  const handle = ApkInstall.addListener?.('downloadProgress', onProgress);
  try {
    return await ApkInstall.downloadApk({ url: apkUrl, sha256 });
  } finally {
    await (await handle)?.remove();
  }
}

/** Hand the verified, downloaded APK to the system installer. */
export async function installVerifiedApk(): Promise<void> {
  const ApkInstall = await getApkInstallPlugin();
  if (!ApkInstall || typeof ApkInstall.installDownloadedApk !== 'function') {
    throw new Error('This build cannot install a downloaded update.');
  }
  await ApkInstall.installDownloadedApk();
}

/** Open the OS screen where "Install unknown apps" is granted. */
export async function openApkInstallSettings(): Promise<boolean> {
  const ApkInstall = await getApkInstallPlugin();
  if (!ApkInstall) return false;
  try {
    await ApkInstall.openInstallSettings();
    return true;
  } catch (err) {
    console.warn('[AppUpdater] Could not open install settings:', err);
    return false;
  }
}

/**
 * Download and launch Android system package installer for APK updates.
 * Uses the native ApkInstall plugin (self-signed, no Play Store). There is no
 * browser fallback: see the note in the body for why.
 */
export async function downloadAndInstallApk(apkUrl: string): Promise<void> {
  if (!apkUrl) return;

  // Off-device there is no package installer to hand off to. The old
  // `window.open(url, '_system')` fallback dumped the user into a browser to
  // fetch a file, which is several steps worse than saying so plainly -- and
  // it silently masked a broken in-app installer as "working".
  if (!Capacitor.isNativePlatform()) {
    toast('Installing the app update needs the Jarvis OS Android app.', {
      id: 'apk-install',
      duration: 6000,
    });
    return;
  }

  const ApkInstall = await getApkInstallPlugin();
  if (!ApkInstall) {
    console.warn('[AppUpdater] ApkInstall plugin is not available on this build.');
    toast('This build cannot install updates in-app. Use the download link instead.', {
      id: 'apk-install',
      duration: 8000,
    });
    return;
  }

  try {
    const can = await ApkInstall.canInstall();
    if (!can.allowed) {
      toast('Turn on “Install unknown apps” for Jarvis OS, then tap Install again.', {
        id: 'apk-install',
        duration: 9000,
      });
      await ApkInstall.openInstallSettings();
      return;
    }
    toast.loading('Downloading update…', { id: 'apk-install', duration: 60000 });
    await ApkInstall.installApk({ url: apkUrl });
    toast.success('Installer opened — tap Install.', { id: 'apk-install', duration: 5000 });
  } catch (err) {
    // No browser fallback on purpose: a silent hand-off to a browser is what
    // made this look broken. Say what happened and leave the link visible.
    console.error('[AppUpdater] In-app APK install failed:', err);
    toast.error('Could not start the installer. Use the download link instead.', {
      id: 'apk-install',
      duration: 8000,
    });
  }
}

/**
 * Ask only "is a newer native APK published?" and nothing else.
 *
 * Deliberately does NOT call `checkForAppUpdates`: on a device that stages an
 * OTA bundle (`next()`) and raises a toast, which would be a nasty surprise
 * just for opening Settings. This probe has no side effects, so the update
 * section can show an accurate notice on arrival instead of only after the
 * user presses "Check Now".
 */
export async function checkApkUpdate(): Promise<ApkUpdateStatus> {
  const origin = getServerOrigin();
  const nativeBuildNumber = await getNativeBuildNumber();

  try {
    const resp = await axios.get<AppVersionInfo>(`${origin}/api/app-updates/version`, { timeout: 7000 });
    const remote = resp.data;
    const apkUrl = remote.apk_url ? resolveUpdateUrl(origin, remote.apk_url) : null;
    const { updateAvailable, indeterminate } = evaluateApk(remote, nativeBuildNumber, apkUrl);

    return {
      updateAvailable,
      indeterminate,
      apkUrl,
      apkVersionCode: remote.apk_version_code,
      apkSha256: remote.apk_sha256 ?? undefined,
      nativeBuildNumber,
      sizeBytes: remote.apk_size_bytes,
    };
  } catch (err) {
    console.error('[AppUpdater] APK update probe failed:', err);
    return {
      updateAvailable: false,
      indeterminate: false,
      apkUrl: null,
      nativeBuildNumber,
      error: 'Could not reach the update server.',
    };
  }
}
