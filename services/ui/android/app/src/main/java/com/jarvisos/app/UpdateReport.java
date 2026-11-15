package com.jarvisos.app;

import android.content.Context;
import android.content.pm.InstallSourceInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.util.Log;

import org.json.JSONObject;

/**
 * Tells the server what happened to an app update on this phone: which way it
 * was installed, what Android answered, and who Android thinks installs Jarvis.
 *
 * Updates were meant to install with no tap, yet a phone still showed Android's
 * install screen and asked for its lock. The phone is the only place that
 * knows why (a refused silent install, another app recorded as Jarvis's
 * installer, the fallback hand-off), so it reports it instead of anyone
 * guessing. Best effort: a report that cannot be sent is only logged.
 */
final class UpdateReport {
    private static final String TAG = "UpdateReport";

    private UpdateReport() {}

    static void send(Context context, String event, JSONObject extra) {
        Context app = context.getApplicationContext();
        new Thread(() -> {
            try {
                JSONObject body = extra != null ? extra : new JSONObject();
                body.put("event", event);
                body.put("build", NativeUpdater.installedCode(app));
                body.put("sdk", Build.VERSION.SDK_INT);
                body.put("app_visible", PushNotifier.appVisible);
                putInstallSource(app, body);
                GuardApi.send(app, "POST", "/api/app-updates/install-report", body.toString());
            } catch (Exception e) {
                Log.w(TAG, "could not report " + event + ": " + e.getMessage());
            }
        }, "update-report").start();
    }

    static JSONObject of(Object... pairs) {
        JSONObject out = new JSONObject();
        try {
            for (int i = 0; i + 1 < pairs.length; i += 2) out.put(String.valueOf(pairs[i]), pairs[i + 1]);
        } catch (Exception ignored) {
            // keys are strings; nothing to fail on
        }
        return out;
    }

    private static void putInstallSource(Context context, JSONObject body) throws Exception {
        PackageManager pm = context.getPackageManager();
        String pkg = context.getPackageName();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            InstallSourceInfo info = pm.getInstallSourceInfo(pkg);
            body.put("installer", info.getInstallingPackageName());
            body.put("initiator", info.getInitiatingPackageName());
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                body.put("update_owner", info.getUpdateOwnerPackageName());
            }
        }
        body.put("may_install", Build.VERSION.SDK_INT < Build.VERSION_CODES.O || pm.canRequestPackageInstalls());
    }
}
