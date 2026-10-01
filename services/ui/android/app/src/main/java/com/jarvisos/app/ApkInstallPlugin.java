package com.jarvisos.app;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.util.Log;
import androidx.core.content.FileProvider;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;

/**
 * Downloads a self-hosted APK and launches the system package installer.
 * No Play Store — works with the release.keystore signature.
 *
 * The download happens here rather than in the WebView so that a real file
 * path is produced (the system installer needs one), so progress can be
 * reported, and so the 12MB never has to cross the JS bridge. The file is
 * verified against the server's SHA-256 before it is offered for install, and
 * {@link #installDownloadedApk()} refuses anything that was not verified.
 */
@CapacitorPlugin(name = "ApkInstall")
public class ApkInstallPlugin extends Plugin {
    private static final String TAG = "ApkInstall";
    private static final String APK_DIR = "apks";
    private static final String APK_FILE = "update.apk";
    /** Only one download in flight; progress events belong to that call. */
    private static volatile String verifiedSha256;

    interface ResultCallback {
        void onDone(String message, Exception error);
    }

    /**
     * Stream the APK to the cache, reporting progress, then verify it against
     * the server-supplied SHA-256. Resolves only for a verified file.
     */
    @PluginMethod
    public void downloadApk(final PluginCall call) {
        final String url = call.getString("url");
        final String expectedSha = call.getString("sha256");
        if (url == null || url.isEmpty()) {
            call.reject("url required");
            return;
        }
        if (expectedSha == null || !expectedSha.matches("(?i)^[0-9a-f]{64}$")) {
            // Installing an APK we cannot authenticate would make the digest
            // check decorative, so this is a hard stop rather than a warning.
            call.reject("A valid sha256 is required to install an update");
            return;
        }
        final Activity activity = getActivity();
        if (activity == null) {
            call.reject("No activity");
            return;
        }
        activity.runOnUiThread(() -> downloadAndVerify(call, url, expectedSha.toLowerCase()));
    }

    /** Install the previously downloaded, verified APK. */
    @PluginMethod
    public void installDownloadedApk(PluginCall call) {
        String sha = verifiedSha256;
        if (sha == null) {
            call.reject("No verified APK has been downloaded yet");
            return;
        }
        File apk = new File(new File(getContext().getCacheDir(), APK_DIR), APK_FILE);
        if (!apk.exists()) {
            verifiedSha256 = null;
            call.reject("The downloaded APK is gone; download it again");
            return;
        }
        try {
            launchInstaller(getContext(), apk);
            JSObject ret = new JSObject();
            ret.put("message", "Installer opened");
            call.resolve(ret);
        } catch (Exception e) {
            Log.e(TAG, "installDownloadedApk failed", e);
            call.reject(e.getMessage() != null ? e.getMessage() : "Install failed", e);
        }
    }

    /** True when a verified APK is sitting in the cache, ready to install. */
    @PluginMethod
    public void isApkDownloaded(PluginCall call) {
        File apk = new File(new File(getContext().getCacheDir(), APK_DIR), APK_FILE);
        JSObject ret = new JSObject();
        ret.put("downloaded", verifiedSha256 != null && apk.exists());
        call.resolve(ret);
    }

    /** Drop the cached APK and the verified marker. */
    @PluginMethod
    public void clearDownloadedApk(PluginCall call) {
        verifiedSha256 = null;
        File apk = new File(new File(getContext().getCacheDir(), APK_DIR), APK_FILE);
        if (apk.exists() && !apk.delete()) {
            Log.w(TAG, "Could not delete cached APK");
        }
        call.resolve();
    }

    private void downloadAndVerify(final PluginCall call, final String url, final String expectedSha) {
        // Off the UI thread: this is a network read plus a hash of 12MB.
        Thread worker = new Thread(() -> {
            File dir = new File(getContext().getCacheDir(), APK_DIR);
            if (!dir.exists() && !dir.mkdirs()) {
                call.reject("Cannot create cache dir");
                return;
            }
            File out = new File(dir, APK_FILE);
            // Not verified until proven, so a failure can never leave a
            // half-written file installable.
            verifiedSha256 = null;
            HttpURLConnection conn = null;
            try {
                conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(60000);
                conn.connect();
                int code = conn.getResponseCode();
                if (code != 200) {
                    call.reject("HTTP " + code);
                    return;
                }
                long total = conn.getContentLengthLong();
                long received = 0;
                long lastEmit = 0;
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                try (InputStream in = conn.getInputStream();
                     FileOutputStream fos = new FileOutputStream(out)) {
                    byte[] buf = new byte[64 * 1024];
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        fos.write(buf, 0, n);
                        digest.update(buf, 0, n);
                        received += n;
                        // Throttle to whole percent changes so a fast download
                        // does not flood the bridge with events.
                        if (total > 0) {
                            long pct = received * 100 / total;
                            if (pct != lastEmit) {
                                lastEmit = pct;
                                emitProgress(received, total, pct, false);
                            }
                        }
                    }
                    fos.flush();
                }
                if (total > 0 && received != total) {
                    // A truncated transfer that still hashed to *something* is
                    // exactly the case a digest alone would not catch.
                    out.delete();
                    call.reject("Download incomplete: got " + received + " of " + total + " bytes");
                    return;
                }
                String actual = toHex(digest.digest());
                if (!actual.equals(expectedSha)) {
                    out.delete();
                    call.reject("Checksum mismatch: expected " + expectedSha + " but got " + actual);
                    return;
                }
                verifiedSha256 = actual;
                emitProgress(received, total > 0 ? total : received, 100, true);
                JSObject ret = new JSObject();
                ret.put("path", out.getAbsolutePath());
                ret.put("sha256", actual);
                ret.put("bytes", received);
                call.resolve(ret);
            } catch (Exception e) {
                if (out.exists() && !out.delete()) {
                    Log.w(TAG, "Could not clean up partial download");
                }
                Log.e(TAG, "downloadAndVerify failed", e);
                call.reject(e.getMessage() != null ? e.getMessage() : "Download failed", e);
            } finally {
                if (conn != null) {
                    conn.disconnect();
                }
            }
        }, "apk-download");
        worker.start();
    }

    private void emitProgress(long received, long total, long percent, boolean done) {
        JSObject payload = new JSObject();
        payload.put("received", received);
        payload.put("total", total);
        payload.put("percent", percent);
        payload.put("done", done);
        notifyListeners("downloadProgress", payload);
    }

    private static String toHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(Character.forDigit((b >> 4) & 0xF, 16));
            sb.append(Character.forDigit(b & 0xF, 16));
        }
        return sb.toString();
    }

    private static void launchInstaller(Context context, File apk) {
        Uri apkUri = FileProvider.getUriForFile(
            context, context.getPackageName() + ".fileprovider", apk);
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(apkUri, "application/vnd.android.package-archive");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
    }

    @PluginMethod
    public void canInstall(PluginCall call) {
        JSObject ret = new JSObject();
        Context ctx = getContext();
        boolean allowed;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            allowed = ctx.getPackageManager().canRequestPackageInstalls();
        } else {
            allowed = true;
        }
        ret.put("allowed", allowed);
        ret.put("packageName", ctx.getPackageName());
        call.resolve(ret);
    }

    @PluginMethod
    public void openInstallSettings(PluginCall call) {
        openInstallSettings();
        call.resolve();
    }

    @PluginMethod
    public void installApk(PluginCall call) {
        String url = call.getString("url");
        if (url == null || url.isEmpty()) {
            call.reject("url required");
            return;
        }
        final PluginCall pending = call;
        final Activity activity = getActivity();
        if (activity == null) {
            call.reject("No activity");
            return;
        }
        activity.runOnUiThread(() -> {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && !getContext().getPackageManager().canRequestPackageInstalls()) {
                openInstallSettings();
                pending.reject("Need 'Install unknown apps' for Jarvis OS — enable it, then try again.");
                return;
            }
            downloadAndInstall(getContext(), url, new ResultCallback() {
                @Override public void onDone(String message, Exception error) {
                    if (error != null) {
                        pending.reject(error.getMessage() != null ? error.getMessage() : "Install failed", error);
                    } else {
                        JSObject ret = new JSObject();
                        ret.put("message", message != null ? message : "ok");
                        pending.resolve(ret);
                    }
                }
            });
        });
    }

    @PluginMethod
    public void getInstalledVersion(PluginCall call) {
        JSObject ret = new JSObject();
        try {
            PackageInfo pi = getContext().getPackageManager()
                .getPackageInfo(getContext().getPackageName(), 0);
            long code = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
                ? pi.getLongVersionCode() : pi.versionCode;
            ret.put("versionCode", code);
            ret.put("versionName", pi.versionName != null ? pi.versionName : "");
        } catch (PackageManager.NameNotFoundException e) {
            ret.put("versionCode", -1);
            ret.put("versionName", "");
        }
        call.resolve(ret);
    }

    private void openInstallSettings() {
        try {
            Intent i = new Intent(
                android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + getContext().getPackageName()));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(i);
        } catch (Exception e) {
            Log.w(TAG, "openInstallSettings failed: " + e.getMessage());
        }
    }

    /**
     * Legacy one-shot download+install. Kept so a JS bundle that predates
     * {@link #downloadApk} still works; it does NOT verify the download, which
     * is why the new flow exists.
     */
    private static void downloadAndInstall(Context context, String url, ResultCallback cb) {
        HttpURLConnection conn = null;
        try {
            File dir = new File(context.getCacheDir(), APK_DIR);
            if (!dir.exists() && !dir.mkdirs()) {
                cb.onDone(null, new Exception("Cannot create cache dir"));
                return;
            }
            File out = new File(dir, APK_FILE);
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(60000);
            conn.connect();
            if (conn.getResponseCode() != 200) {
                cb.onDone(null, new Exception("HTTP " + conn.getResponseCode()));
                return;
            }
            try (InputStream in = conn.getInputStream();
                 FileOutputStream fos = new FileOutputStream(out)) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) fos.write(buf, 0, n);
            }
            launchInstaller(context, out);
            cb.onDone("Installer opened", null);
        } catch (Exception e) {
            Log.e(TAG, "downloadAndInstall failed", e);
            cb.onDone(null, e);
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }
}
