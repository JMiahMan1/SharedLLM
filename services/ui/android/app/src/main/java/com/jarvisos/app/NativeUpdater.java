package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.os.Build;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;

/**
 * Keeps the app up to date with nobody touching the phone.
 *
 * Before this, only the web page looked for a new APK, and only when the app
 * was opened fresh (or every six hours while a WebView timer survived in the
 * background), so a deploy reached a phone whenever someone happened to
 * relaunch Jarvis. This runs natively instead: from the always-on push
 * service every {@link #CHECK_EVERY_MS}, from a WorkManager job for phones
 * where that service is not running, and when the app comes to the front.
 *
 * It asks the gateway's {@code /api/app-updates/version} for the APK on offer,
 * and when its version code is newer than this build it downloads the file,
 * checks it against the published SHA-256, and hands it to {@link SelfUpdate}
 * (a session install that needs no tap on Android 12+ once Jarvis installed
 * itself). It never installs while the app is on screen or during a Drop In
 * call: the download is kept and installed once the app is in the background.
 */
final class NativeUpdater {
    private static final String TAG = "NativeUpdater";
    static final long CHECK_EVERY_MS = 15 * 60_000L;
    /** One failed version is not retried more often than this. */
    private static final long RETRY_FAILED_MS = 60 * 60_000L;
    private static final String APK_DIR = "native_update";
    private static final String APK_FILE = "jarvis-update.apk";
    private static final String KEY_STAGED_CODE = "native_update_staged_code";
    private static final String KEY_TRIED = "native_update_tried";
    private static final Object LOCK = new Object();
    private static volatile long lastCheck;

    private NativeUpdater() {}

    /** Check now unless a check ran within {@code minGapMs}; safe from any thread but the UI one. */
    static void maybeCheck(Context context, long minGapMs) {
        long now = System.currentTimeMillis();
        if (now - lastCheck < minGapMs) return;
        lastCheck = now;
        check(context.getApplicationContext());
    }

    /** Check on a background thread (the activity's resume, the boot receiver). */
    static void checkSoon(Context context, long minGapMs) {
        Context app = context.getApplicationContext();
        new Thread(() -> maybeCheck(app, minGapMs), "native-update").start();
    }

    /** The app left the screen: install an update that was waiting for that. */
    static void onAppHidden(Context context) {
        Context app = context.getApplicationContext();
        new Thread(() -> {
            synchronized (LOCK) {
                installStaged(app);
            }
        }, "native-update-install").start();
    }

    static void check(Context context) {
        synchronized (LOCK) {
            try {
                String server = serverUrl(context);
                if (server.isEmpty()) return;
                JSONObject offer = new JSONObject(get(server + "/api/app-updates/version"));
                if (!offer.optBoolean("apk_available", false)) return;
                long code = offer.optLong("apk_version_code", 0);
                String url = offer.optString("apk_url", "");
                String sha = offer.optString("apk_sha256", "").toLowerCase();
                if (code <= 0 || url.isEmpty() || sha.length() != 64) return;
                if (code <= installedCode(context)) {
                    clearStaged(context);
                    return;
                }
                SharedPreferences prefs = GuardApi.prefs(context);
                if (prefs.getLong(KEY_STAGED_CODE, 0) != code || !stagedFile(context).exists()) {
                    String tried = prefs.getString(KEY_TRIED, "");
                    long now = System.currentTimeMillis();
                    if (tried.startsWith(code + ":")) {
                        long at = Long.parseLong(tried.substring(tried.indexOf(':') + 1));
                        if (now - at < RETRY_FAILED_MS) return;
                    }
                    prefs.edit().putString(KEY_TRIED, code + ":" + now).apply();
                    download(url, sha, stagedFile(context));
                    prefs.edit().putLong(KEY_STAGED_CODE, code).apply();
                    Log.i(TAG, "APK " + code + " downloaded and verified");
                }
                installStaged(context);
            } catch (Exception e) {
                Log.w(TAG, "update check failed: " + e.getClass().getSimpleName() + ": " + e.getMessage());
            }
        }
    }

    /** Install the verified download, unless the person is using Jarvis or on a call. */
    private static void installStaged(Context context) {
        long code = GuardApi.prefs(context).getLong(KEY_STAGED_CODE, 0);
        File apk = stagedFile(context);
        if (code <= 0 || !apk.exists()) return;
        if (code <= installedCode(context)) {
            clearStaged(context);
            return;
        }
        if (SelfUpdate.playProtectBlocked(context) == code) {
            return; // Play Protect refused this one: retrying would only ask again (Settings explains)
        }
        if (SelfUpdate.needsOk(context) == code) {
            return; // Android wants the person's OK for this one: Settings offers it, nothing pops up
        }
        if (PushNotifier.appVisible || DropInCall.isActive() || SelfUpdate.inProgress(context)) {
            Log.i(TAG, "APK " + code + " waits: Jarvis is in use");
            return;
        }
        if (SelfUpdate.install(context, apk, "background")) {
            Log.i(TAG, "APK " + code + " handed to the installer");
        }
    }

    /** Forget a failed version, so the next check tries it at once. */
    static void forgetFailures(Context context) {
        GuardApi.prefs(context).edit().remove(KEY_TRIED).apply();
    }

    /** The version code of the verified download waiting to be installed, or 0. */
    static long stagedCode(Context context) {
        return GuardApi.prefs(context).getLong(KEY_STAGED_CODE, 0);
    }

    private static void clearStaged(Context context) {
        GuardApi.prefs(context).edit().remove(KEY_STAGED_CODE).apply();
        File apk = stagedFile(context);
        if (apk.exists() && !apk.delete()) Log.w(TAG, "could not remove an old update file");
    }

    private static File stagedFile(Context context) {
        return new File(new File(context.getCacheDir(), APK_DIR), APK_FILE);
    }

    static long installedCode(Context context) {
        try {
            PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= Build.VERSION_CODES.P ? info.getLongVersionCode() : info.versionCode;
        } catch (Exception e) {
            return Long.MAX_VALUE; // cannot tell: never install over an unknown build
        }
    }

    private static String serverUrl(Context context) {
        String url = TokenBridgePlugin.prefs(context).getString(TokenBridgePlugin.KEY_SERVER_URL, "");
        url = url == null ? "" : url.trim();
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        return url;
    }

    private static String get(String url) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        try {
            conn.setConnectTimeout(15_000);
            conn.setReadTimeout(30_000);
            int status = conn.getResponseCode();
            if (status != 200) throw new IllegalStateException("HTTP " + status + " from " + url);
            try (InputStream in = conn.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                return out.toString("UTF-8");
            }
        } finally {
            conn.disconnect();
        }
    }

    /** Download to {@code out}, or delete it and throw if it is not the published file. */
    private static void download(String url, String expectedSha, File out) throws Exception {
        File dir = out.getParentFile();
        if (dir != null && !dir.exists() && !dir.mkdirs()) throw new IllegalStateException("no cache dir");
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        try {
            conn.setConnectTimeout(15_000);
            conn.setReadTimeout(60_000);
            int status = conn.getResponseCode();
            if (status != 200) throw new IllegalStateException("HTTP " + status + " downloading the APK");
            long total = conn.getContentLengthLong();
            long received = 0;
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream in = conn.getInputStream(); FileOutputStream fos = new FileOutputStream(out)) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) {
                    fos.write(buf, 0, n);
                    digest.update(buf, 0, n);
                    received += n;
                }
            }
            String actual = hex(digest.digest());
            if ((total > 0 && received != total) || !actual.equals(expectedSha)) {
                if (!out.delete()) Log.w(TAG, "could not remove a bad download");
                throw new IllegalStateException("the download did not match the published checksum");
            }
        } catch (Exception e) {
            if (out.exists() && !out.delete()) Log.w(TAG, "could not remove a partial download");
            throw e;
        } finally {
            conn.disconnect();
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(Character.forDigit((b >> 4) & 0xF, 16));
            sb.append(Character.forDigit(b & 0xF, 16));
        }
        return sb.toString();
    }
}
