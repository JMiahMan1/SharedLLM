package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import com.jarvisos.app.widgets.WidgetApi;
import com.jarvisos.app.widgets.WidgetUpdater;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * One incremental Health Connect import: read since the last good sync (with
 * a 48 h overlap), post to the existing ingestion APIs, remember how far it
 * got. The server's external_id replay makes the overlap harmless.
 *
 * Destinations: sleep sessions to {@code /api/health/sleep}; resting heart
 * rate, HRV and weight to {@code /api/health/metrics}; walks, runs and rides
 * to {@code /api/geo/workouts/import} (geo owns workouts). Steps are not
 * imported -- the pedometer already posts them.
 *
 * The data belongs to whoever connected it: when a different person is
 * signed in on the phone, nothing is posted under their account.
 */
public final class HealthConnectSync {
    private static final String TAG = "HealthConnectSync";
    static final String PREFS = "jarvis_health_connect";
    static final String KEY_ENABLED = "enabled";
    static final String KEY_OWNER = "owner";
    static final String KEY_LAST_SYNC = "last_sync";
    static final String KEY_LAST_ATTEMPT = "last_attempt";
    static final String KEY_LAST_RESULT = "last_result";

    private static final int METRIC_CHUNK = 500;
    private static final int WORKOUT_CHUNK = 200;

    private HealthConnectSync() {}

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static boolean enabled(Context context) {
        return prefs(context).getBoolean(KEY_ENABLED, false);
    }

    /** Connected by {@code owner}: syncing is on and runs for that person only. */
    static void enable(Context context, String owner) {
        prefs(context).edit()
            .putBoolean(KEY_ENABLED, true)
            .putString(KEY_OWNER, owner.trim().toLowerCase(Locale.ROOT))
            .apply();
        HealthConnectSyncWorker.schedule(context);
    }

    /** Stops syncing and forgets the cursor; what was imported stays on the server. */
    static void disable(Context context) {
        prefs(context).edit().clear().apply();
        HealthConnectSyncWorker.cancel(context);
    }

    static JSONObject lastResult(Context context) {
        String raw = prefs(context).getString(KEY_LAST_RESULT, null);
        if (raw == null) return null;
        try {
            return new JSONObject(raw);
        } catch (Exception e) {
            return null;
        }
    }

    private static JSONObject failure(Context context, String error, String detail) {
        JSONObject out = new JSONObject();
        try {
            out.put("ok", false);
            out.put("error", error);
            if (detail != null) out.put("detail", detail);
            out.put("at", System.currentTimeMillis());
        } catch (Exception ignored) {
            // JSONObject.put only throws on a null key.
        }
        remember(context, out);
        return out;
    }

    private static void remember(Context context, JSONObject result) {
        prefs(context).edit()
            .putLong(KEY_LAST_ATTEMPT, System.currentTimeMillis())
            .putString(KEY_LAST_RESULT, result.toString())
            .apply();
    }

    /**
     * Runs one sync. {@code inBackground} is true from the worker: without the
     * background-read grant Health Connect refuses reads while the app is
     * closed, so the worker waits for the next app open instead of failing.
     */
    public static synchronized JSONObject run(Context context, boolean inBackground) {
        Context app = context.getApplicationContext();
        if (!enabled(app)) return failure(app, "not_connected", null);
        if (!HealthConnectReader.available(app)) return failure(app, "unavailable", null);

        WidgetApi.ensureCredentials(app);
        String key = WidgetApi.apiKey(app);
        if (key == null || key.isEmpty()) return failure(app, "signed_out", null);
        String owner = prefs(app).getString(KEY_OWNER, "");
        String signedIn = TokenBridgePlugin.prefs(app).getString(TokenBridgePlugin.KEY_USERNAME, "");
        if (signedIn == null || !owner.equals(signedIn.trim().toLowerCase(Locale.ROOT))) {
            return failure(app, "different_account", "Connected by " + owner);
        }

        Set<String> granted;
        try {
            granted = HealthConnectReader.grantedPermissions(app);
        } catch (Exception e) {
            return failure(app, "permissions_unreadable", e.getMessage());
        }
        boolean anyData = false;
        for (String p : HealthConnectReader.dataPermissions()) {
            if (granted.contains(p)) anyData = true;
        }
        if (!anyData) return failure(app, "no_permission", null);
        if (inBackground && !granted.contains(HealthConnectReader.BACKGROUND)) {
            return failure(app, "waiting_for_app", null);
        }

        long now = System.currentTimeMillis();
        long last = prefs(app).getLong(KEY_LAST_SYNC, 0L);
        long start = HealthConnectMapping.windowStart(last, now);
        HealthConnectReader.Batch batch;
        try {
            batch = HealthConnectReader.read(app, start, now, granted);
        } catch (Exception e) {
            return failure(app, "read_failed", e.getMessage());
        }

        JSONObject out = new JSONObject();
        try {
            int sleepNew = 0;
            int sleepDup = 0;
            int rejected = 0;
            for (JSONObject night : batch.sleeps) {
                JSONObject resp = postOrRefused(app, "/api/health/sleep", night);
                if (resp == null) rejected++;
                else if (resp.optBoolean("duplicate", false)) sleepDup++;
                else sleepNew++;
            }

            int metricNew = 0;
            int metricDup = 0;
            for (List<JSONObject> chunk : chunks(batch.metrics, METRIC_CHUNK)) {
                JSONObject body = new JSONObject();
                body.put("samples", new JSONArray(chunk));
                JSONObject resp = postOrRefused(app, "/api/health/metrics", body);
                if (resp == null) {
                    rejected += chunk.size();
                    continue;
                }
                metricNew += resp.optInt("accepted", 0);
                metricDup += resp.optInt("duplicates", 0);
            }

            int workoutNew = 0;
            int workoutDup = 0;
            int workoutSkipped = 0;
            for (List<JSONObject> chunk : chunks(batch.workouts, WORKOUT_CHUNK)) {
                JSONObject body = new JSONObject();
                body.put("source", HealthConnectMapping.SOURCE);
                body.put("workouts", new JSONArray(chunk));
                JSONObject resp = postOrRefused(app, "/api/geo/workouts/import", body);
                if (resp == null) {
                    rejected += chunk.size();
                    continue;
                }
                workoutNew += resp.optInt("imported", 0);
                workoutDup += resp.optInt("duplicates", 0);
                JSONArray skipped = resp.optJSONArray("skipped");
                workoutSkipped += skipped == null ? 0 : skipped.length();
            }

            out.put("ok", batch.errors.isEmpty());
            out.put("at", now);
            out.put("from", start);
            out.put("sleep", counts(sleepNew, sleepDup));
            out.put("metrics", counts(metricNew, metricDup));
            JSONObject workouts = counts(workoutNew, workoutDup);
            workouts.put("skipped", workoutSkipped);
            workouts.put("other_types", batch.otherWorkouts);
            out.put("workouts", workouts);
            out.put("rejected", rejected);
            if (!batch.errors.isEmpty()) {
                out.put("error", "partial_read");
                out.put("detail", new JSONArray(batch.errors));
            }
        } catch (Exception e) {
            Log.w(TAG, "posting failed: " + e.getMessage());
            return failure(app, "post_failed", e.getMessage());
        }

        // Only a complete read moves the cursor, so a type that failed is
        // read again from the same place next time.
        if (batch.errors.isEmpty()) {
            prefs(app).edit().putLong(KEY_LAST_SYNC, now).apply();
        }
        remember(app, out);
        WidgetUpdater.requestAll(app);
        return out;
    }

    /**
     * The response, or null when the server refused the payload itself (a
     * 4xx: a reading outside its rules). A refusal is counted and skipped so
     * one odd record cannot pin the cursor forever; anything else (offline,
     * a 5xx) throws and the whole sync is retried later from the same place.
     */
    private static JSONObject postOrRefused(Context app, String path, JSONObject body) throws Exception {
        try {
            return WidgetApi.post(app, path, body);
        } catch (IllegalStateException e) {
            String msg = e.getMessage() == null ? "" : e.getMessage();
            if (msg.startsWith("HTTP 4") && !msg.startsWith("HTTP 401") && !msg.startsWith("HTTP 403")
                    && !msg.startsWith("HTTP 404")) {
                Log.w(TAG, path + " refused: " + msg);
                return null;
            }
            throw e;
        }
    }

    private static JSONObject counts(int added, int duplicates) throws Exception {
        JSONObject c = new JSONObject();
        c.put("added", added);
        c.put("duplicates", duplicates);
        return c;
    }

    private static List<List<JSONObject>> chunks(List<JSONObject> rows, int size) {
        java.util.ArrayList<List<JSONObject>> out = new java.util.ArrayList<>();
        for (int i = 0; i < rows.size(); i += size) {
            out.add(rows.subList(i, Math.min(rows.size(), i + size)));
        }
        return out;
    }
}
