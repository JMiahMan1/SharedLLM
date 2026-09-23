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

/**
 * Downloads a self-hosted APK and launches the system package installer.
 * No Play Store — works with the release.keystore signature.
 */
@CapacitorPlugin(name = "ApkInstall")
public class ApkInstallPlugin extends Plugin {
    private static final String TAG = "ApkInstall";
    private static final String APK_DIR = "apks";

    interface ResultCallback {
        void onDone(String message, Exception error);
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

    private static void downloadAndInstall(Context context, String url, ResultCallback cb) {
        try {
            File dir = new File(context.getCacheDir(), APK_DIR);
            if (!dir.exists() && !dir.mkdirs()) {
                cb.onDone(null, new Exception("Cannot create cache dir"));
                return;
            }
            File out = new File(dir, "update.apk");
            HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
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
            conn.disconnect();

            Uri apkUri = FileProvider.getUriForFile(
                context, context.getPackageName() + ".fileprovider", out);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(apkUri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(intent);
            cb.onDone("Installer opened", null);
        } catch (Exception e) {
            Log.e(TAG, "downloadAndInstall failed", e);
            cb.onDone(null, e);
        }
    }
}
