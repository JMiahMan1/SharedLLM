package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Tells the server what this phone did with a Drop In ("answering", "rang")
 * and why, so a Drop In that did not connect can be explained from the
 * gateway's log instead of guessed at. Best effort, off the main thread, and
 * never in the way of the call itself.
 */
final class DropInReport {
    private static final String TAG = "DropInReport";
    private static final int TIMEOUT_MS = 8000;

    private DropInReport() {}

    private static long appBuild(Context context) {
        try {
            return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).getLongVersionCode();
        } catch (Exception e) {
            return 0;
        }
    }

    static void send(Context context, String room, String event, String detail) {
        Context app = context.getApplicationContext();
        new Thread(() -> {
            HttpURLConnection conn = null;
            try {
                SharedPreferences p = TokenBridgePlugin.prefs(app);
                String token = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
                String server = p.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
                if (token == null || server == null || token.isEmpty() || server.isEmpty()) return;
                JSONObject body = new JSONObject()
                        .put("event", event)
                        .put("detail", detail == null ? "" : detail)
                        .put("device_key", p.getString(TokenBridgePlugin.KEY_DEVICE_KEY, ""))
                        .put("android", android.os.Build.VERSION.SDK_INT)
                        .put("app_build", appBuild(app));
                URL url = new URL(server.replaceAll("/+$", "") + "/api/communication/calls/" + room + "/device-report");
                conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setConnectTimeout(TIMEOUT_MS);
                conn.setReadTimeout(TIMEOUT_MS);
                conn.setDoOutput(true);
                conn.setRequestProperty("Authorization", "Bearer " + token);
                conn.setRequestProperty("Content-Type", "application/json");
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                }
                conn.getResponseCode();
            } catch (Exception e) {
                Log.w(TAG, "could not report " + event + ": " + e.getMessage());
            } finally {
                if (conn != null) conn.disconnect();
            }
        }, "drop-in-report").start();
    }
}
