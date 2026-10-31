package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import com.jarvisos.app.widgets.WidgetApi;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Activity events the phone noticed by itself (a chat notification, what
 * another app started playing), kept on the phone until the server takes them.
 *
 * They used to be posted once from a throwaway thread, so a phone that was
 * offline, or a server that was restarting, lost them. Here each event is
 * written to disk first, then sent; it leaves the queue once the server took
 * it (or refused it outright). The queue is flushed when something is added
 * and on every background sync run ({@link CommsSyncWorker}).
 */
final class ActivityOutbox {
    private static final String TAG = "ActivityOutbox";
    private static final String PREFS = "jarvis_activity_outbox";
    private static final String KEY_PENDING = "pending";
    /** The most events kept while the server cannot be reached; the oldest go first. */
    static final int MAX_PENDING = 300;

    private static final Object LOCK = new Object();
    private static final AtomicBoolean FLUSHING = new AtomicBoolean(false);

    private ActivityOutbox() {}

    /** Keep one {@code /api/activity} body and try to send it now, off the calling thread. */
    static void add(Context context, JSONObject body) {
        Context app = context.getApplicationContext();
        try {
            // When it happened, so the feed and the recap place it right even if
            // it is sent hours later (telemetry reads detail.at).
            JSONObject detail = body.optJSONObject("detail");
            if (detail == null) {
                detail = new JSONObject();
                body.put("detail", detail);
            }
            if (!detail.has("at")) detail.put("at", System.currentTimeMillis());
        } catch (Exception e) {
            return;
        }
        synchronized (LOCK) {
            JSONArray pending = pending(app);
            while (pending.length() >= MAX_PENDING) pending.remove(0);
            pending.put(body);
            prefs(app).edit().putString(KEY_PENDING, pending.toString()).apply();
        }
        new Thread(() -> flush(app)).start();
    }

    /**
     * Send what is waiting, oldest first; stop at the first one that could not
     * be sent (offline, a 5xx, signed out) and keep it and the rest. Returns
     * false when something is still waiting for that reason.
     */
    static boolean flush(Context context) {
        Context app = context.getApplicationContext();
        if (!FLUSHING.compareAndSet(false, true)) return true;
        java.util.List<String> sent = new java.util.ArrayList<>();
        boolean ok = true;
        try {
            JSONArray pending;
            synchronized (LOCK) {
                pending = pending(app);
            }
            if (pending.length() == 0) return true;
            WidgetApi.ensureCredentials(app);
            String key = WidgetApi.apiKey(app);
            if (key == null || key.isEmpty()) return true;
            for (int i = 0; i < pending.length(); i++) {
                JSONObject item = pending.optJSONObject(i);
                if (item == null) continue;
                if (!send(app, item)) {
                    ok = false;
                    break;
                }
                sent.add(item.toString());
            }
        } finally {
            if (!sent.isEmpty()) {
                synchronized (LOCK) {
                    // By content, not position: events may have been added (and
                    // the oldest trimmed) while this ran.
                    JSONArray now = pending(app);
                    JSONArray left = new JSONArray();
                    for (int i = 0; i < now.length(); i++) {
                        Object item = now.opt(i);
                        if (item == null || !sent.remove(item.toString())) left.put(item);
                    }
                    prefs(app).edit().putString(KEY_PENDING, left.toString()).apply();
                }
            }
            FLUSHING.set(false);
        }
        return ok;
    }

    /** True once the server took the event or refused it for good. */
    private static boolean send(Context app, JSONObject item) {
        try {
            WidgetApi.post(app, "/api/activity", item);
            return true;
        } catch (IllegalStateException e) {
            String msg = e.getMessage() == null ? "" : e.getMessage();
            if (msg.startsWith("HTTP 4") && !msg.startsWith("HTTP 401") && !msg.startsWith("HTTP 403")
                    && !msg.startsWith("HTTP 404")) {
                Log.w(TAG, "activity refused, dropped: " + msg);
                return true;
            }
            return false;
        } catch (Exception e) {
            return false;
        }
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static JSONArray pending(Context context) {
        try {
            return new JSONArray(prefs(context).getString(KEY_PENDING, "[]"));
        } catch (Exception e) {
            return new JSONArray();
        }
    }
}
