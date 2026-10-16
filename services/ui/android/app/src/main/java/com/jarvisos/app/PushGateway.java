package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import com.jarvisos.app.widgets.WidgetApi;
import java.util.Collections;
import java.util.Map;
import java.util.UUID;
import org.json.JSONObject;

/**
 * Calls to the gateway's {@code /api/push/*} routes with the person's own
 * API key and this install's device id (docs/PUSH.md).
 */
final class PushGateway {
    static final String STREAM = "/api/push/stream";
    static final String ACTION = "/api/push/action";
    static final String UP_REGISTER = "/api/push/up/register";
    static final String UP_UNREGISTER = "/api/push/up/unregister";
    static final String UP_LIST = "/api/push/up";

    private PushGateway() {}

    /**
     * A random id made once per install and kept through sign-outs, so the
     * gateway can tell this phone's UnifiedPush registrations from another's.
     */
    static synchronized String deviceId(Context context) {
        SharedPreferences p = TokenBridgePlugin.prefs(context);
        String id = p.getString(TokenBridgePlugin.KEY_DEVICE_ID, null);
        if (id == null || id.isEmpty()) {
            id = UUID.randomUUID().toString();
            p.edit().putString(TokenBridgePlugin.KEY_DEVICE_ID, id).apply();
        }
        return id;
    }

    static Map<String, String> headers(Context context) {
        return Collections.singletonMap("X-Device-Id", deviceId(context));
    }

    /** POST as the signed-in person. Network trouble surfaces as an IOException. */
    static JSONObject post(Context context, String path, JSONObject body) throws Exception {
        return request(context, "POST", path, body);
    }

    /** GET as the signed-in person. Network trouble surfaces as an IOException. */
    static JSONObject get(Context context, String path) throws Exception {
        return request(context, "GET", path, null);
    }

    private static JSONObject request(Context context, String method, String path, JSONObject body)
            throws Exception {
        WidgetApi.ensureCredentials(context);
        String key = WidgetApi.apiKey(context);
        if (key == null || key.isEmpty()) throw new IllegalStateException("Not signed in");
        return WidgetApi.request(WidgetApi.serverUrl(context), key, method, path, body, headers(context));
    }

    /** POST with credentials given explicitly (the key being signed out). */
    static JSONObject post(Context context, String server, String key, String path, JSONObject body)
            throws Exception {
        String base = server.endsWith("/") ? server.substring(0, server.length() - 1) : server;
        return WidgetApi.request(base, key, "POST", path, body, headers(context));
    }
}
