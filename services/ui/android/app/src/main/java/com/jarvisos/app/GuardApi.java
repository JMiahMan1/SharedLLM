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
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
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
    /** The user apps on the phone when the guard first ran: never held for approval. */
    static final String KEY_INSTALL_BASELINE = "guard_install_baseline";
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

    /**
     * Locked now: the server said so and the lock has not run out since, or
     * bedtime or school time has started on the clock since the last status.
     */
    static boolean isLocked(Context context) {
        JSONObject s = lastStatus(context);
        if (!s.optBoolean("locked", false)) {
            return LockWarning.lockedByClock(System.currentTimeMillis(), nextLockAt(context), nextLockReason(context));
        }
        long until = epochMs(s.optString("until", ""));
        return until == 0L || System.currentTimeMillis() < until;
    }

    static String lockReason(Context context) {
        JSONObject s = lastStatus(context);
        if (!s.optBoolean("locked", false)
                && LockWarning.lockedByClock(System.currentTimeMillis(), nextLockAt(context), nextLockReason(context))) {
            return nextLockReason(context);
        }
        return s.optString("reason", "");
    }

    /** When the phone next locks on its own (epoch ms), or 0 when nothing is coming. */
    static long nextLockAt(Context context) {
        return epochMs(lastStatus(context).optString("next_lock_at", ""));
    }

    static String nextLockReason(Context context) {
        String reason = lastStatus(context).optString("next_lock_reason", "");
        return "null".equals(reason) ? "" : reason;
    }

    /** The household day the status was for (YYYY-MM-DD). */
    static String statusDay(Context context) {
        return lastStatus(context).optString("day", "");
    }

    /** Minutes left today for each app with its own limit. */
    static Map<String, Integer> appMinutesLeft(Context context) {
        Map<String, Integer> out = new HashMap<>();
        JSONObject left = lastStatus(context).optJSONObject("app_minutes_left");
        if (left == null) return out;
        for (Iterator<String> it = left.keys(); it.hasNext(); ) {
            String pkg = it.next();
            out.put(pkg, left.optInt(pkg));
        }
        return out;
    }

    /** New apps wait for a parent's OK (the server's word; off until it has spoken). */
    static boolean installApprovalRequired(Context context) {
        return lastStatus(context).optBoolean("install_approval", false);
    }

    /** The parents' answers on new apps: package to pending, approved or denied. */
    static Map<String, String> appApprovals(Context context) {
        return stringMap(lastStatus(context).optJSONObject("app_approvals"));
    }

    /** The parents' answers on app permissions: "package|group" to pending, approved or denied. */
    static Map<String, String> permissionApprovals(Context context) {
        return stringMap(lastStatus(context).optJSONObject("permission_approvals"));
    }

    /** The kinds of permission a parent approves first (camera, location, ...). */
    static Set<String> permissionGating(Context context) {
        return strings(lastStatus(context).optJSONArray("permission_gating"));
    }

    private static Map<String, String> stringMap(JSONObject o) {
        Map<String, String> out = new HashMap<>();
        if (o == null) return out;
        for (Iterator<String> it = o.keys(); it.hasNext(); ) {
            String key = it.next();
            out.put(key, o.optString(key));
        }
        return out;
    }

    /**
     * The apps on the phone when the guard first ran on it; set once, so an
     * app installed later is new. Returns the baseline.
     */
    static Set<String> installBaseline(Context context, Set<String> installedNow) {
        SharedPreferences p = prefs(context);
        Set<String> saved = p.getStringSet(KEY_INSTALL_BASELINE, null);
        if (saved != null) return new HashSet<>(saved);
        p.edit().putStringSet(KEY_INSTALL_BASELINE, new HashSet<>(installedNow)).apply();
        return new HashSet<>(installedNow);
    }

    /**
     * Ask a parent first: a new app ("app", package), an app's permission
     * ("permission", "package|group") or a blocked site ("site", domain).
     * Call off the main thread. Returns an error to show, or null.
     */
    static String askParent(Context context, String kind, String target, String label) {
        try {
            JSONObject body = new JSONObject();
            body.put("kind", kind);
            body.put("target", target);
            if (label != null && !label.isEmpty()) body.put("label", label);
            HttpResult r = send(context, "POST", "/api/guard/requests", body.toString());
            if (r == null) return "No connection to Jarvis. Try again when the phone is online.";
            if (r.status == 200) return null;
            String detail = new JSONObject(r.body.isEmpty() ? "{}" : r.body).optString("detail", "");
            return detail.isEmpty() ? "That did not work." : detail;
        } catch (Exception e) {
            return "That did not work.";
        }
    }

    static Set<String> alwaysAllowed(Context context) {
        return strings(lastStatus(context).optJSONArray("always_allowed"));
    }

    /** Apps blocked now (a parent switched them off, or their own minutes ran out). */
    static Set<String> blockedApps(Context context) {
        return strings(lastStatus(context).optJSONArray("blocked_apps"));
    }

    /** A JSON list of strings as a set (empty when missing). */
    static Set<String> strings(JSONArray list) {
        Set<String> out = new HashSet<>();
        if (list != null) {
            for (int i = 0; i < list.length(); i++) out.add(list.optString(i));
        }
        return out;
    }

    /** An ISO-8601 time from the server as epoch ms; 0 when empty or unreadable. */
    static long epochMs(String iso) {
        if (iso == null || iso.isEmpty() || "null".equals(iso)) return 0L;
        try {
            return java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli();
        } catch (Exception e) {
            return 0L;
        }
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
                long until = epochMs(new JSONObject(r.body).optString("until", ""));
                long ms = until == 0L ? System.currentTimeMillis() + 10 * 60_000L : until;
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
