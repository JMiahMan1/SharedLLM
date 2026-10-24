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
                prefs(context).edit().putLong(KEY_UNLOCKED_UNTIL, ms).apply();
                return null;
            }
            String detail = new JSONObject(r.body.isEmpty() ? "{}" : r.body).optString("detail", "");
            return detail.isEmpty() ? "That did not work." : detail;
        } catch (Exception e) {
            return "That did not work.";
        }
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
