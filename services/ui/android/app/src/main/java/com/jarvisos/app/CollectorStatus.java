package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

/**
 * How each collector's last run went, kept on the phone for the health report
 * ({@link DeviceHealth}). Only states and counts: never a number, a name or a
 * message's words.
 *
 * Collectors: {@code comms} (calls and texts, every run), {@code usage}
 * (screen time) and {@code listener} (a comms run the notification listener
 * started when a text or call came in).
 */
final class CollectorStatus {
    static final String PREFS = "jarvis_collector_health";
    static final String[] NAMES = {"comms", "usage", "listener"};
    private static final int MESSAGE_MAX = 200;

    private CollectorStatus() {}

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /**
     * One run's outcome. {@code outcome} is a short code ({@code ok},
     * {@code signed_out}, {@code no_permission}, {@code upload_failed});
     * {@code message} says more in words, without any collected content.
     */
    static void record(Context context, String name, boolean ok, String outcome, String message) {
        long now = System.currentTimeMillis();
        String m = message == null ? "" : message;
        if (m.length() > MESSAGE_MAX) m = m.substring(0, MESSAGE_MAX);
        SharedPreferences.Editor ed = prefs(context).edit()
            .putLong(name + "_at", now)
            .putBoolean(name + "_ok", ok)
            .putString(name + "_outcome", outcome == null ? "" : outcome)
            .putString(name + "_message", m);
        if (ok) ed.putLong(name + "_last_ok_at", now);
        ed.apply();
    }

    /** Every collector that has run at least once, as the health report sends it. */
    static JSONObject toJson(Context context) {
        SharedPreferences p = prefs(context);
        JSONObject out = new JSONObject();
        for (String name : NAMES) {
            long at = p.getLong(name + "_at", 0L);
            if (at <= 0L) continue;
            try {
                JSONObject run = new JSONObject()
                    .put("at", at)
                    .put("ok", p.getBoolean(name + "_ok", false))
                    .put("outcome", p.getString(name + "_outcome", ""))
                    .put("message", p.getString(name + "_message", ""));
                long lastOk = p.getLong(name + "_last_ok_at", 0L);
                if (lastOk > 0L) run.put("last_ok_at", lastOk);
                out.put(name, run);
            } catch (Exception ignored) {
                // Plain keys and values: org.json does not throw here.
            }
        }
        return out;
    }
}
