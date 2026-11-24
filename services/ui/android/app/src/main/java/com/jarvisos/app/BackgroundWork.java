package com.jarvisos.app;

import android.content.Context;
import android.util.Log;

import com.jarvisos.app.widgets.WidgetApi;

import org.json.JSONObject;

/**
 * Keeps the collecting workers scheduled.
 *
 * Signing out cancels {@link UsageSyncWorker}, {@link CommsSyncWorker} and
 * {@link PhotoScanWorker} (TokenBridgePlugin). They used to be scheduled only
 * when MainActivity was created, so signing out and in again inside the
 * running app, or a reboot or an update after a sign-out, left a phone that
 * never collected calls, texts or screen time again until someone reopened
 * the app from scratch. Every place that can notice the app is signed in
 * calls {@link #ensureScheduled}: the activity, sign-in, boot and update, the
 * app-update worker, and the notification listener (which Android keeps bound
 * whatever the app does).
 */
final class BackgroundWork {
    private static final String TAG = "BackgroundWork";
    /** Comms runs the notification listener starts (at most one at a time, every 30 s). */
    private static final CollectRules.Gate LISTENER_RUN = new CollectRules.Gate(CollectRules.LISTENER_RUN_GAP_MS);
    /** The listener's hourly re-arm. */
    private static final CollectRules.Gate REARM = new CollectRules.Gate(CollectRules.REARM_GAP_MS);
    /** Lets a text reach the SMS store (or a call the call log) before reading it. */
    private static final long SETTLE_MS = 5_000L;

    private BackgroundWork() {}

    /**
     * Schedule the collecting workers when someone is signed in. KEEP policy:
     * a worker already scheduled is left alone, so this is safe to call often.
     * Returns whether they are (now) scheduled.
     */
    static boolean ensureScheduled(Context context) {
        Context app = context.getApplicationContext();
        try {
            WidgetApi.ensureCredentials(app);
            String key = WidgetApi.apiKey(app);
            if (key == null || key.isEmpty()) return false;
            // Screen time while the app is closed (a no-op until usage access is on).
            UsageSyncWorker.schedule(app);
            // Calls and texts the same way (a no-op without their permissions).
            CommsSyncWorker.schedule(app);
            // New photos and videos on a protected child's phone (a no-op until
            // the media permission is on and a parent keeps scanning enabled).
            PhotoScanWorker.schedule(app);
            return true;
        } catch (Exception e) {
            Log.w(TAG, "could not schedule the collecting workers", e);
            return false;
        }
    }

    /** {@link #ensureScheduled}, at most once an hour (the notification listener's check). */
    static void ensureScheduledHourly(Context context) {
        if (!REARM.tryStart(System.currentTimeMillis())) return;
        try {
            ensureScheduled(context);
        } finally {
            REARM.finish();
        }
    }

    /**
     * A text or call notification just came in: collect calls and texts now
     * rather than at the next periodic run. Off the main thread, at most one
     * run in flight and not more than every 30 seconds; the periodic worker
     * stays the backstop for anything that brings no notification.
     */
    static void collectCommsSoon(Context context) {
        Context app = context.getApplicationContext();
        if (!LISTENER_RUN.tryStart(System.currentTimeMillis())) return;
        try {
            new Thread(() -> {
                try {
                    Thread.sleep(SETTLE_MS);
                    JSONObject result = CommsCollector.run(app);
                    boolean ok = result.optBoolean("ok", true);
                    CollectorStatus.record(app, "listener", ok, result.optString("outcome", ok ? "ok" : "upload_failed"),
                        "Started by a notification: " + result.optInt("calls", 0) + " calls, "
                            + result.optInt("sms", 0) + " texts");
                    DeviceHealth.reportIfDue(app);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                } catch (Exception e) {
                    Log.w(TAG, "listener comms run failed: " + e.getMessage());
                } finally {
                    LISTENER_RUN.finish();
                }
            }, "listener-comms").start();
        } catch (RuntimeException e) {
            LISTENER_RUN.finish();
            Log.w(TAG, "could not start the listener comms run", e);
        }
    }
}
