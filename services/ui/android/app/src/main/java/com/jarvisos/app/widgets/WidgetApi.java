package com.jarvisos.app.widgets;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;
import com.jarvisos.app.TokenBridgePlugin;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/** Minimal HTTPS client for home-screen widgets (no WebView / no OkHttp). */
public final class WidgetApi {
    private static final String TAG = "JarvisWidgetApi";
    private static final int TIMEOUT_MS = 12000;
    public static final String DEFAULT_SERVER = "https://jarvis.sumemail.com";

    private WidgetApi() {}

    public static SharedPreferences prefs(Context context) {
        return TokenBridgePlugin.prefs(context);
    }

    public static String apiKey(Context context) {
        return prefs(context).getString(TokenBridgePlugin.KEY_API_KEY, null);
    }

    public static String internalSecret(Context context) {
        return prefs(context).getString(TokenBridgePlugin.KEY_INTERNAL_SECRET, null);
    }

    public static String serverUrl(Context context) {
        String s = prefs(context).getString(TokenBridgePlugin.KEY_SERVER_URL, null);
        if (s == null || s.isEmpty()) return DEFAULT_SERVER;
        return s.endsWith("/") ? s.substring(0, s.length() - 1) : s;
    }

    /**
     * Re-sync credentials from Capacitor Preferences (current app session).
     * CapacitorStorage is the source of truth so configure screens always use
     * the logged-in API key even if TokenBridge mirror is stale/empty.
     */
    public static void ensureCredentials(Context context) {
        SharedPreferences p = prefs(context);
        try {
            SharedPreferences cap = context.getSharedPreferences("CapacitorStorage", Context.MODE_PRIVATE);
            String key = cap.getString("jarvis_api_key", null);
            String url = cap.getString("jarvis_server_url", null);
            String secret = cap.getString("internal_secret", null);
            SharedPreferences.Editor ed = p.edit();
            boolean changed = false;
            if (key != null && !key.isEmpty() && !key.equals(p.getString(TokenBridgePlugin.KEY_API_KEY, null))) {
                ed.putString(TokenBridgePlugin.KEY_API_KEY, key);
                changed = true;
                Log.i(TAG, "Synced jarvis_api_key from CapacitorStorage");
            }
            if (url != null && !url.isEmpty() && !url.equals(p.getString(TokenBridgePlugin.KEY_SERVER_URL, null))) {
                ed.putString(TokenBridgePlugin.KEY_SERVER_URL, url);
                changed = true;
            }
            if (secret != null && !secret.isEmpty() && !secret.equals(p.getString(TokenBridgePlugin.KEY_INTERNAL_SECRET, null))) {
                ed.putString(TokenBridgePlugin.KEY_INTERNAL_SECRET, secret);
                changed = true;
            }
            // Fallback: TokenBridge mirror may already hold a key if CapacitorStorage is empty
            if (key == null || key.isEmpty()) {
                String mirrored = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
                if (mirrored == null || mirrored.isEmpty()) {
                    String legacy = cap.getString("jarvis_api_key", null);
                    if (legacy != null && !legacy.isEmpty()) {
                        ed.putString(TokenBridgePlugin.KEY_API_KEY, legacy);
                        changed = true;
                    }
                }
            }
            if (changed) ed.apply();
        } catch (Exception e) {
            Log.w(TAG, "ensureCredentials failed: " + e.getMessage());
        }
    }

    /** Server-side entity search with explicit query + limit (default browse cap is 200). */
    public static JSONObject searchEntities(Context context, String query, int limit) throws Exception {
        JSONObject body = new JSONObject();
        body.put("query", query != null ? query : "");
        body.put("domain", JSONObject.NULL);
        body.put("area", JSONObject.NULL);
        body.put("state", JSONObject.NULL);
        body.put("limit", limit);
        body.put("controllable_only", true);
        JSONObject resp = post(context, "/execute/entity/search", body);
        JSONArray result = resp.optJSONArray("result");
        if (result == null) {
            JSONObject detail = resp.optJSONObject("detail");
            if (detail != null) result = detail.optJSONArray("entities");
        }
        if (result == null) {
            if (!resp.has("result") && !resp.has("detail")) {
                throw new IllegalStateException("Unexpected entity search response");
            }
            return new JSONObject();
        }
        JSONObject byId = new JSONObject();
        for (int i = 0; i < result.length(); i++) {
            JSONObject e = result.getJSONObject(i);
            String id = e.optString("entity_id");
            if (!id.isEmpty()) byId.put(id, e);
        }
        return byId;
    }

    public static boolean isAwayFromHome(Context context, float thresholdMeters) {
        SharedPreferences p = prefs(context);
        float homeLat = p.getFloat(TokenBridgePlugin.KEY_HOME_LAT, (float) TokenBridgePlugin.DEFAULT_HOME_LAT);
        float homeLng = p.getFloat(TokenBridgePlugin.KEY_HOME_LNG, (float) TokenBridgePlugin.DEFAULT_HOME_LNG);
        if (!p.contains(TokenBridgePlugin.KEY_LAST_LAT)) {
            // No fix yet — treat as home so we don't nag with confirms on first use
            return false;
        }
        float lastLat = p.getFloat(TokenBridgePlugin.KEY_LAST_LAT, homeLat);
        float lastLng = p.getFloat(TokenBridgePlugin.KEY_LAST_LNG, homeLng);
        long ts = p.getLong(TokenBridgePlugin.KEY_LAST_TS, 0L);
        if (System.currentTimeMillis() - ts > 30 * 60 * 1000L) {
            // Stale fix — don't force confirm
            return false;
        }
        return haversineM(homeLat, homeLng, lastLat, lastLng) > thresholdMeters;
    }

    public static float haversineM(double lat1, double lng1, double lat2, double lng2) {
        double R = 6371000.0;
        double dLat = Math.toRadians(lat2 - lat1);
        double dLng = Math.toRadians(lng2 - lng1);
        double a = Math.sin(dLat / 2) * Math.sin(dLat / 2)
            + Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2))
            * Math.sin(dLng / 2) * Math.sin(dLng / 2);
        return (float) (R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
    }

    public static JSONObject request(Context context, String method, String path, JSONObject body) throws Exception {
        ensureCredentials(context);
        String key = apiKey(context);
        if (key == null || key.isEmpty()) {
            throw new IllegalStateException("Not signed in");
        }
        String base = serverUrl(context);
        URL url = new URL(base + path);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        try {
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(TIMEOUT_MS);
            conn.setRequestMethod(method);
            conn.setRequestProperty("Authorization", "Bearer " + key);
            String secret = internalSecret(context);
            if (secret != null && !secret.isEmpty()) {
                conn.setRequestProperty("X-Internal-Secret", secret);
            }
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("Accept", "application/json");
            if (body != null) {
                conn.setDoOutput(true);
                byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
                try (OutputStream os = conn.getOutputStream()) {
                    os.write(data);
                }
            }
            int code = conn.getResponseCode();
            InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            if (is == null) {
                throw new IllegalStateException("HTTP " + code);
            }
            StringBuilder sb = new StringBuilder();
            try (BufferedReader br = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8))) {
                String line;
                while ((line = br.readLine()) != null) sb.append(line);
            }
            if (code >= 400) {
                throw new IllegalStateException("HTTP " + code + ": " + sb);
            }
            String text = sb.toString().trim();
            if (text.isEmpty()) return new JSONObject();
            JSONObject json = new JSONObject(text);
            // Surface execution FAILURE payloads (e.g. HA not configured) instead of empty results
            String status = json.optString("status", "");
            if ("FAILURE".equals(status) || "ERROR".equals(status)) {
                String msg = json.optString("message", "request failed");
                Log.w(TAG, path + " FAILURE: " + msg);
                throw new IllegalStateException(msg);
            }
            return json;
        } finally {
            conn.disconnect();
        }
    }

    public static JSONObject post(Context context, String path, JSONObject body) throws Exception {
        return request(context, "POST", path, body);
    }

    public static JSONObject get(Context context, String path) throws Exception {
        return request(context, "GET", path, null);
    }

    /** Pinned device entity_ids from device_control widget settings. */
    public static List<String> pinnedDevices(Context context) {
        List<String> out = new ArrayList<>();
        try {
            JSONObject resp = get(context, "/api/widgets/settings");
            JSONArray widgets = resp.optJSONArray("widgets");
            if (widgets == null) return out;
            for (int i = 0; i < widgets.length(); i++) {
                JSONObject w = widgets.getJSONObject(i);
                if (!"device_control".equals(w.optString("widget_key"))) continue;
                JSONArray pins = w.optJSONArray("pinned_devices");
                if (pins == null) return out;
                for (int j = 0; j < pins.length(); j++) {
                    out.add(pins.getString(j));
                }
                break;
            }
        } catch (Exception e) {
            Log.w(TAG, "pinnedDevices failed: " + e.getMessage());
        }
        return out;
    }

    /** Current states for entity_ids via entity search (all domains when empty). */
    public static JSONObject entityStates(Context context, List<String> entityIds) throws Exception {
        JSONObject body = new JSONObject();
        body.put("query", "");
        // Omit null keys so Pydantic receives explicit nulls only when present;
        // JSONObject.put(key, null) removes the key on Android's org.json.
        body.put("domain", JSONObject.NULL);
        body.put("area", JSONObject.NULL);
        body.put("state", JSONObject.NULL);
        // Default server limit is 200 — homes routinely exceed that (e.g. 693).
        body.put("limit", 1000);
        JSONObject resp = post(context, "/execute/entity/search", body);
        JSONObject byId = new JSONObject();
        JSONArray result = resp.optJSONArray("result");
        if (result == null) {
            JSONObject detail = resp.optJSONObject("detail");
            if (detail != null) result = detail.optJSONArray("entities");
        }
        if (result == null) {
            // Distinguish "empty" from "wrong shape" so widgets can show a useful error
            if (!resp.has("result") && !resp.has("detail")) {
                throw new IllegalStateException("Unexpected entity search response");
            }
            return byId;
        }
        for (int i = 0; i < result.length(); i++) {
            JSONObject e = result.getJSONObject(i);
            String id = e.optString("entity_id");
            if (entityIds == null || entityIds.isEmpty() || entityIds.contains(id)) {
                byId.put(id, e);
            }
        }
        return byId;
    }

    public static JSONObject haService(Context context, String domain, String service, String entityId) throws Exception {
        JSONObject body = new JSONObject();
        body.put("domain", domain);
        body.put("service", service);
        body.put("entity_id", entityId);
        body.put("service_data", JSONObject.NULL);
        return post(context, "/execute/ha_service", body);
    }

    public static JSONObject mediaTransport(Context context, String command) throws Exception {
        JSONObject body = new JSONObject();
        body.put("command", command);
        return post(context, "/execute/media/transport", body);
    }

    public static JSONObject mediaStatus(Context context) throws Exception {
        return post(context, "/execute/media/status", new JSONObject());
    }

    public static String friendlyName(JSONObject entity) {
        String fn = entity.optString("friendly_name");
        if (fn != null && !fn.isEmpty()) return fn;
        String id = entity.optString("entity_id", "");
        int dot = id.indexOf('.');
        return dot >= 0 ? id.substring(dot + 1).replace('_', ' ') : id;
    }

    public static boolean isActiveState(String state) {
        if (state == null) return false;
        String s = state.toLowerCase();
        return s.equals("on") || s.equals("playing") || s.equals("open")
            || s.equals("home") || s.equals("unlocked") || s.equals("cleaning")
            || s.equals("heat") || s.equals("cool") || s.equals("auto");
    }

    public static String toggleService(String domain, String state) {
        boolean active = isActiveState(state);
        if ("cover".equals(domain)) return active ? "close_cover" : "open_cover";
        if ("lock".equals(domain)) return active ? "lock" : "unlock";
        if ("light".equals(domain) || "switch".equals(domain)
            || "media_player".equals(domain) || "fan".equals(domain)) {
            return active ? "turn_off" : "turn_on";
        }
        return active ? "turn_off" : "turn_on";
    }

    /** Resolve toggle for a fixed action button service against current state. */
    public static String resolveService(String configuredService, String domain, String state) {
        if (configuredService == null || configuredService.isEmpty()
            || "toggle".equals(configuredService)) {
            return toggleService(domain, state);
        }
        if ("open_cover".equals(configuredService) || "close_cover".equals(configuredService)
            || "lock".equals(configuredService) || "unlock".equals(configuredService)
            || "press".equals(configuredService) || "turn_on".equals(configuredService)
            || "turn_off".equals(configuredService)) {
            return configuredService;
        }
        return toggleService(domain, state);
    }
}
