package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Mirrors Capacitor Preferences credentials into a dedicated SharedPreferences
 * file that AppWidgets / WorkManager / BroadcastReceivers can read without
 * starting the WebView. Also stores last-known location for geofence checks.
 */
@CapacitorPlugin(name = "TokenBridge")
public class TokenBridgePlugin extends Plugin {

    public static final String PREFS = "jarvis_widget";
    public static final String KEY_API_KEY = "jarvis_api_key";
    public static final String KEY_SERVER_URL = "jarvis_server_url";
    /**
     * Who this device is. Mirrored here so LocationTrackingService can attribute
     * an upload after the WebView is gone -- without it, background location
     * could authenticate but not say whose position it was.
     */
    public static final String KEY_USERNAME = "jarvis_username";
    public static final String KEY_USER_ID = "jarvis_user_id";
    public static final String KEY_LAST_LAT = "last_lat";
    public static final String KEY_LAST_LNG = "last_lng";
    public static final String KEY_LAST_TS = "last_ts";
    public static final String KEY_HOME_LAT = "home_lat";
    public static final String KEY_HOME_LNG = "home_lng";
    /** This install's id for the push stream; kept through sign-outs (PushGateway). */
    public static final String KEY_DEVICE_ID = "push_device_id";
    /** The last ntfy message id read from the push stream (PushService). */
    public static final String KEY_PUSH_SINCE = "push_since";
    /** The outbox long poll's cursor while the push stream is unavailable (PushFallback). */
    public static final String KEY_POLL_CURSOR = "push_poll_cursor";

    // zone.home from Home Assistant
    public static final double DEFAULT_HOME_LAT = 33.16670697948413;
    public static final double DEFAULT_HOME_LNG = -111.56466007232666;

    public static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    @PluginMethod
    public void setCredentials(PluginCall call) {
        String apiKey = call.getString("apiKey");
        String serverUrl = call.getString("serverUrl");
        SharedPreferences p = prefs(getContext());
        String previousKey = p.getString(KEY_API_KEY, null);
        String previousServer = p.getString(KEY_SERVER_URL, null);
        SharedPreferences.Editor ed = p.edit();
        // Clients use their own API key only; drop any internal secret an
        // older build stored here.
        ed.remove("internal_secret");
        if (apiKey != null) {
            if (apiKey.isEmpty()) {
                ed.remove(KEY_API_KEY);
            } else {
                ed.putString(KEY_API_KEY, apiKey);
            }
        }
        boolean accountChanged = apiKey != null && !apiKey.isEmpty()
                && previousKey != null && !previousKey.isEmpty() && !apiKey.equals(previousKey);
        boolean serverChanged = serverUrl != null && !serverUrl.isEmpty() && !serverUrl.equals(previousServer);
        if ((apiKey != null && !apiKey.equals(previousKey)) || serverChanged) {
            // Another person's (or server's) stream starts from now, not from this one's place.
            ed.remove(KEY_PUSH_SINCE);
            ed.remove(KEY_POLL_CURSOR);
        }
        if (serverUrl != null) {
            if (serverUrl.isEmpty()) {
                ed.remove(KEY_SERVER_URL);
            } else {
                ed.putString(KEY_SERVER_URL, serverUrl);
            }
        }
        ed.apply();
        if (apiKey != null) {
            // Signing in starts notifications; signing out ends them and tells
            // apps using Jarvis for UnifiedPush that their endpoints are gone.
            if (apiKey.isEmpty()) {
                PushService.stop(getContext());
                UnifiedPushDistributor.signOut(getContext(), previousServer, previousKey);
            } else {
                if (accountChanged) {
                    // The previous person's notices go, so none of their
                    // buttons can act as the new one.
                    PushNotifier.clearAll(getContext());
                    UnifiedPushDistributor.switchAccount(getContext(), previousServer, previousKey);
                }
                PushService.start(getContext());
            }
        }
        if ((accountChanged || serverChanged) && p.getString(KEY_API_KEY, null) != null) {
            // A running stream re-reads the credentials only when it reconnects.
            PushService.reconnect(getContext());
        }
        call.resolve();
    }

    @PluginMethod
    public void setIdentity(PluginCall call) {
        String username = call.getString("username");
        String userId = call.getString("userId");
        SharedPreferences.Editor ed = prefs(getContext()).edit();
        if (username != null) {
            if (username.isEmpty()) {
                ed.remove(KEY_USERNAME);
            } else {
                ed.putString(KEY_USERNAME, username);
            }
        }
        if (userId != null) {
            if (userId.isEmpty()) {
                ed.remove(KEY_USER_ID);
            } else {
                ed.putString(KEY_USER_ID, userId);
            }
        }
        ed.apply();
        call.resolve();
    }

    @PluginMethod
    public void getCredentials(PluginCall call) {
        JSObject ret = new JSObject();
        SharedPreferences p = prefs(getContext());
        ret.put("apiKey", p.getString(KEY_API_KEY, null));
        ret.put("serverUrl", p.getString(KEY_SERVER_URL, null));
        call.resolve(ret);
    }

    @PluginMethod
    public void setLastLocation(PluginCall call) {
        Double lat = call.getDouble("latitude");
        Double lng = call.getDouble("longitude");
        if (lat == null || lng == null) {
            call.reject("latitude and longitude required");
            return;
        }
        prefs(getContext()).edit()
            .putFloat(KEY_LAST_LAT, lat.floatValue())
            .putFloat(KEY_LAST_LNG, lng.floatValue())
            .putLong(KEY_LAST_TS, System.currentTimeMillis())
            .apply();
        call.resolve();
    }

    @PluginMethod
    public void setHome(PluginCall call) {
        Double lat = call.getDouble("latitude");
        Double lng = call.getDouble("longitude");
        if (lat == null || lng == null) {
            call.reject("latitude and longitude required");
            return;
        }
        prefs(getContext()).edit()
            .putFloat(KEY_HOME_LAT, lat.floatValue())
            .putFloat(KEY_HOME_LNG, lng.floatValue())
            .apply();
        call.resolve();
    }

    @PluginMethod
    public void refreshWidgets(PluginCall call) {
        com.jarvisos.app.widgets.WidgetUpdater.requestAll(getContext());
        call.resolve();
    }
}
