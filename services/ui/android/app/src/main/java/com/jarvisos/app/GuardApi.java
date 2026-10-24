package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;

/**
 * The screen guard's calls to the Jarvis server, made as the phone's signed-in
 * member (the token TokenBridge mirrors from the web app), and the last answer
 * kept so the guard still knows the rules when the phone is offline.
 */
final class GuardApi {

    static final String KEY_STATUS = "guard_status";
    static final String KEY_UNLOCKED_UNTIL = "guard_unlocked_until";
    /** The parents' offline-code verifiers, from the last online parent unlock. */
    static final String KEY_OFFLINE_CODES = "guard_offline_codes";
    static final String KEY_OFFLINE_FAILS = "guard_offline_fails";
    static final String KEY_OFFLINE_REPORT_DUE = "guard_offline_report_due";
    static final int OFFLINE_MAX_FAILS = 5;
    static final long OFFLINE_WAIT_MS = 10 * 60_000L;

    private GuardApi() {}

    static SharedPreferences prefs(Context context) {
        return TokenBridgePlugin.prefs(context);
    }

    /** Fetch the live screen-time status and remember it; false when it could not. */
    static boolean refreshStatus(Context context) {
        SharedPreferences p = prefs(context);
        String user = p.getString(TokenBridgePlugin.KEY_USERNAME, null);
        if (user == null || user.isEmpty()) return false;
        try {
            String body = request(context, "GET",
                    "/api/screen-time/" + URLEncoder.encode(user, "UTF-8") + "/status", null);
            if (body == null) return false;
            new JSONObject(body); // only remember an answer that parses
            p.edit().putString(KEY_STATUS, body).apply();
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    static JSONObject lastStatus(Context context) {
        try {
            return new JSONObject(prefs(context).getString(KEY_STATUS, "{}"));
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    static boolean isProtected(Context context) {
        return lastStatus(context).optBoolean("protected", false);
    }

    /** Locked now: the server said so and the lock has not run out since. */
    static boolean isLocked(Context context) {
        JSONObject s = lastStatus(context);
        if (!s.optBoolean("locked", false)) return false;
        String until = s.optString("until", "");
        if (until.isEmpty()) return true;
        try {
            return System.currentTimeMillis() < java.time.OffsetDateTime.parse(until).toInstant().toEpochMilli();
        } catch (Exception e) {
            return true;
        }
    }

    static String lockReason(Context context) {
        return lastStatus(context).optString("reason", "");
    }

    static Set<String> alwaysAllowed(Context context) {
        Set<String> out = new HashSet<>();
        JSONArray apps = lastStatus(context).optJSONArray("always_allowed");
        if (apps != null) {
            for (int i = 0; i < apps.length(); i++) out.add(apps.optString(i));
        }
        return out;
    }

    /** Apps blocked now (a parent switched them off, or their own minutes ran out). */
    static Set<String> blockedApps(Context context) {
        Set<String> out = new HashSet<>();
        JSONArray apps = lastStatus(context).optJSONArray("blocked_apps");
        if (apps != null) {
            for (int i = 0; i < apps.length(); i++) out.add(apps.optString(i));
        }
        return out;
    }

    static boolean isUnlocked(Context context) {
        return System.currentTimeMillis() < prefs(context).getLong(KEY_UNLOCKED_UNTIL, 0L);
    }

    /** A parent's own login lifts the guard for the window the server grants. Returns an error to show, or null. */
    static String parentUnlock(Context context, String username, String password) {
        try {
            JSONObject body = new JSONObject();
            body.put("username", username);
            body.put("password", password);
            HttpResult r = send(context, "POST", "/api/guard/parent-unlock", body.toString());
            if (r == null) return "No connection to Jarvis. Try again when the phone is online.";
            if (r.status == 200) {
                String until = new JSONObject(r.body).optString("until", "");
                long ms = until.isEmpty()
                        ? System.currentTimeMillis() + 10 * 60_000L
                        : java.time.OffsetDateTime.parse(until).toInstant().toEpochMilli();
                JSONObject answer = new JSONObject(r.body);
                SharedPreferences.Editor edit = prefs(context).edit().putLong(KEY_UNLOCKED_UNTIL, ms);
                JSONArray codes = answer.optJSONArray("offline_codes");
                if (codes != null) edit.putString(KEY_OFFLINE_CODES, codes.toString());
                edit.apply();
                return null;
            }
            String detail = new JSONObject(r.body.isEmpty() ? "{}" : r.body).optString("detail", "");
            return detail.isEmpty() ? "That did not work." : detail;
        } catch (Exception e) {
            return "That did not work.";
        }
    }

    /**
     * A parent's offline code, typed with no connection. Five wrong codes wait
     * ten minutes, like the online unlock. A good code lifts the guard for ten
     * minutes and the parents are told once the phone is back online.
     * Returns an error to show, or null.
     */
    static String offlineUnlock(Context context, String code) {
        SharedPreferences p = prefs(context);
        long now = System.currentTimeMillis();
        java.util.List<Long> fails = new java.util.ArrayList<>();
        for (String t : p.getString(KEY_OFFLINE_FAILS, "").split(",")) {
            try {
                long at = Long.parseLong(t.trim());
                if (now - at < OFFLINE_WAIT_MS) fails.add(at);
            } catch (NumberFormatException ignored) {
                // empty or damaged entry
            }
        }
        if (fails.size() >= OFFLINE_MAX_FAILS) return "Too many tries. Wait ten minutes, then try again.";
        JSONArray codes;
        try {
            codes = new JSONArray(p.getString(KEY_OFFLINE_CODES, "[]"));
        } catch (Exception e) {
            codes = new JSONArray();
        }
        if (codes.length() == 0) {
            return "No offline code is set up on this phone yet. A parent signs in here once while online to set it up.";
        }
        for (int i = 0; i < codes.length(); i++) {
            JSONObject c = codes.optJSONObject(i);
            if (c != null && OfflineCode.matches(code, c.optString("salt"), c.optInt("iterations"), c.optString("hash"))) {
                p.edit()
                        .putLong(KEY_UNLOCKED_UNTIL, now + 10 * 60_000L)
                        .putString(KEY_OFFLINE_FAILS, "")
                        .putBoolean(KEY_OFFLINE_REPORT_DUE, true)
                        .apply();
                reportOfflineUnlockIfDue(context);
                return null;
            }
        }
        fails.add(now);
        StringBuilder joined = new StringBuilder();
        for (Long at : fails) joined.append(joined.length() == 0 ? "" : ",").append(at);
        p.edit().putString(KEY_OFFLINE_FAILS, joined.toString()).apply();
        return "That code is not right.";
    }

    /** An offline unlock is never silent: tell the parents as soon as the server answers. */
    static void reportOfflineUnlockIfDue(Context context) {
        if (!prefs(context).getBoolean(KEY_OFFLINE_REPORT_DUE, false)) return;
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("event", "offline_unlock");
                HttpResult r = send(context, "POST", "/api/guard/tamper", body.toString());
                if (r != null && r.status == 200) {
                    prefs(context).edit().putBoolean(KEY_OFFLINE_REPORT_DUE, false).apply();
                }
            } catch (Exception ignored) {
                // Still offline: the next status refresh tries again.
            }
        }).start();
    }

    /** Sites visited and blocked since the last report, onto the activity feed (parents only). */
    static void reportWeb(Context context, java.util.Map<String, int[]> counts) {
        new Thread(() -> {
            for (java.util.Map.Entry<String, int[]> e : counts.entrySet()) {
                try {
                    JSONObject body = new JSONObject();
                    body.put("feature", "web");
                    body.put("item", e.getKey());
                    body.put("event", e.getValue()[1] > 0 ? "blocked" : "visit");
                    JSONObject detail = new JSONObject();
                    detail.put("visits", e.getValue()[0]);
                    detail.put("blocked", e.getValue()[1]);
                    body.put("detail", detail);
                    send(context, "POST", "/api/activity", body.toString());
                } catch (Exception ignored) {
                    // Best effort: a missed batch is not retried.
                }
            }
        }).start();
    }

    /** Whether the parents switched the web filter on for this phone. */
    static boolean webFilterWanted(Context context) {
        JSONObject rules = lastStatus(context).optJSONObject("rules");
        JSONObject wf = rules == null ? null : rules.optJSONObject("web_filter");
        return wf != null && wf.optBoolean("enabled", false);
    }

    /** Tell the parents protection changed; best effort, never throws. */
    static void reportTamper(Context context, String event) {
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("event", event);
                send(context, "POST", "/api/guard/tamper", body.toString());
            } catch (Exception ignored) {
                // The parents' notice is best effort; the guard itself keeps working.
            }
        }).start();
    }

    private static String request(Context context, String method, String path, String json) throws Exception {
        HttpResult r = send(context, method, path, json);
        return r != null && r.status == 200 ? r.body : null;
    }

    static final class HttpResult {
        final int status;
        final String body;

        HttpResult(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    private static HttpResult send(Context context, String method, String path, String json) throws Exception {
        SharedPreferences p = prefs(context);
        String token = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
        String server = p.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
        if (token == null || server == null) return null;
        HttpURLConnection conn = (HttpURLConnection) new URL(server.replaceAll("/+$", "") + path).openConnection();
        try {
            conn.setRequestMethod(method);
            conn.setConnectTimeout(10_000);
            conn.setReadTimeout(15_000);
            conn.setRequestProperty("Authorization", "Bearer " + token);
            if (json != null) {
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json");
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(json.getBytes(StandardCharsets.UTF_8));
                }
            }
            int status = conn.getResponseCode();
            InputStream in = status >= 400 ? conn.getErrorStream() : conn.getInputStream();
            String body = "";
            if (in != null) {
                try (InputStream s = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                    byte[] buf = new byte[4096];
                    int n;
                    while ((n = s.read(buf)) > 0) out.write(buf, 0, n);
                    body = out.toString("UTF-8");
                }
            }
            return new HttpResult(status, body);
        } finally {
            conn.disconnect();
        }
    }
}
