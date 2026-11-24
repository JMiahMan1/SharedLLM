package com.jarvisos.app;

import android.Manifest;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.provider.Settings;
import android.util.Log;

import androidx.work.WorkInfo;
import androidx.work.WorkManager;

import com.jarvisos.app.widgets.WidgetApi;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

import org.json.JSONObject;

/**
 * This phone's collection health, for the parents: which permissions are
 * granted, whether each collecting worker is scheduled, and how each
 * collector's last run went (POST /api/telemetry/device_health). Sent on app
 * start, after sign-in and from the collecting workers.
 *
 * Only states, counts and times. Never a message, a number or a name.
 */
final class DeviceHealth {
    private static final String TAG = "DeviceHealth";
    static final String PATH = "/api/telemetry/device_health";
    private static final String PREFS = "jarvis_collector_health";
    private static final String KEY_REPORTED_AT = "health_reported_at";
    /** Reports from the workers come at most this often (two workers can run together). */
    static final long MIN_GAP_MS = 10L * 60 * 1000;

    private DeviceHealth() {}

    /** Report now, off the calling thread (app start, sign-in). */
    static void reportAsync(Context context) {
        Context app = context.getApplicationContext();
        try {
            new Thread(() -> report(app), "device-health").start();
        } catch (RuntimeException e) {
            Log.w(TAG, "could not start the health report", e);
        }
    }

    /** {@link #reportIfDue}, off the calling thread (sign-in, which the web app repeats on every start). */
    static void reportSoon(Context context) {
        Context app = context.getApplicationContext();
        try {
            new Thread(() -> reportIfDue(app), "device-health").start();
        } catch (RuntimeException e) {
            Log.w(TAG, "could not start the health report", e);
        }
    }

    /** Report unless one went out in the last ten minutes (on a worker's thread). */
    static void reportIfDue(Context context) {
        Context app = context.getApplicationContext();
        long last = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(KEY_REPORTED_AT, 0L);
        long now = System.currentTimeMillis();
        if (now >= last && now - last < MIN_GAP_MS) return;
        report(app);
    }

    /** Build and send the report; never throws (blocks on the network: not on the main thread). */
    static void report(Context context) {
        Context app = context.getApplicationContext();
        try {
            WidgetApi.ensureCredentials(app);
            String key = WidgetApi.apiKey(app);
            if (key == null || key.isEmpty()) return;
            WidgetApi.post(app, PATH, build(app));
            SharedPreferences.Editor ed = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit();
            ed.putLong(KEY_REPORTED_AT, System.currentTimeMillis()).apply();
        } catch (Exception e) {
            // An older server (404) or offline: the next run tries again.
            Log.w(TAG, "health report not sent: " + e.getMessage());
        }
    }

    static JSONObject build(Context app) throws Exception {
        JSONObject body = new JSONObject();
        body.put("device_id", PushGateway.deviceId(app));
        body.put("device_name", LocationTrackingService.deviceName());
        try {
            PackageInfo pi = app.getPackageManager().getPackageInfo(app.getPackageName(), 0);
            body.put("app_version", pi.versionName == null ? "" : pi.versionName);
            body.put("app_build", pi.getLongVersionCode());
        } catch (Exception e) {
            body.put("app_version", "");
        }
        body.put("signed_in", true);
        JSONObject perms = new JSONObject();
        perms.put("read_sms", granted(app, Manifest.permission.READ_SMS));
        perms.put("read_call_log", granted(app, Manifest.permission.READ_CALL_LOG));
        perms.put("usage_access", PhoneUsagePlugin.hasUsageAccess(app));
        perms.put("notification_listener", notificationListenerOn(app));
        perms.put("location", granted(app, Manifest.permission.ACCESS_FINE_LOCATION)
            || granted(app, Manifest.permission.ACCESS_COARSE_LOCATION));
        body.put("permissions", perms);
        JSONObject workers = new JSONObject();
        workers.put("comms_sync", workerState(app, CommsSyncWorker.WORK_NAME));
        workers.put("usage_sync", workerState(app, UsageSyncWorker.WORK_NAME));
        workers.put("photo_scan", workerState(app, PhotoScanWorker.WORK_NAME));
        body.put("workers", workers);
        body.put("collectors", CollectorStatus.toJson(app));
        body.put("reported_at", System.currentTimeMillis());
        return body;
    }

    private static boolean granted(Context app, String permission) {
        return app.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    /** Whether Jarvis's notification listener (social activity, chat texts, media) is turned on. */
    static boolean notificationListenerOn(Context app) {
        try {
            String enabled = Settings.Secure.getString(app.getContentResolver(), "enabled_notification_listeners");
            if (enabled == null || enabled.isEmpty()) return false;
            return enabled.contains(new ComponentName(app, MediaSessionListenerService.class).flattenToString());
        } catch (Exception e) {
            return false;
        }
    }

    /** ENQUEUED / RUNNING / CANCELLED / NONE ... for one unique periodic work. */
    private static String workerState(Context app, String name) {
        try {
            List<WorkInfo> infos = WorkManager.getInstance(app).getWorkInfosForUniqueWork(name).get(5, TimeUnit.SECONDS);
            List<String> states = new ArrayList<>();
            for (WorkInfo info : infos) states.add(info.getState().name());
            return CollectRules.summarize(states);
        } catch (Exception e) {
            // Could not ask: left out of the report rather than called unscheduled.
            return "UNKNOWN";
        }
    }
}
