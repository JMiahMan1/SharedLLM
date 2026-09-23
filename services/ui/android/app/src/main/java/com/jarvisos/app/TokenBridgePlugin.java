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
    public static final String KEY_INTERNAL_SECRET = "internal_secret";
    public static final String KEY_LAST_LAT = "last_lat";
    public static final String KEY_LAST_LNG = "last_lng";
    public static final String KEY_LAST_TS = "last_ts";
    public static final String KEY_HOME_LAT = "home_lat";
    public static final String KEY_HOME_LNG = "home_lng";

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
        String internalSecret = call.getString("internalSecret");
        SharedPreferences.Editor ed = prefs(getContext()).edit();
        if (apiKey != null) {
            if (apiKey.isEmpty()) {
                ed.remove(KEY_API_KEY);
            } else {
                ed.putString(KEY_API_KEY, apiKey);
            }
        }
        if (serverUrl != null) {
            if (serverUrl.isEmpty()) {
                ed.remove(KEY_SERVER_URL);
            } else {
                ed.putString(KEY_SERVER_URL, serverUrl);
            }
        }
        if (internalSecret != null) {
            if (internalSecret.isEmpty()) {
                ed.remove(KEY_INTERNAL_SECRET);
            } else {
                ed.putString(KEY_INTERNAL_SECRET, internalSecret);
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
