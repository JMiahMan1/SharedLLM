package com.jarvisos.app;

import android.content.Context;
import android.content.Intent;
import android.os.Build;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Starts and stops {@link LocationTrackingService}.
 *
 * The JS side cannot keep location alive on its own: a WebView watch dies with
 * the process. It still owns the decision to track (user preference, consent,
 * permission state), so it calls in here when that decision is made.
 */
@CapacitorPlugin(name = "LocationTracking")
public class LocationTrackingPlugin extends Plugin {

    private static final int NOTIFICATION_ID = 0x10A1;

    @PluginMethod
    public void start(PluginCall call) {
        String username = call.getString("username");
        if (username == null || username.trim().isEmpty()) {
            // Without this the service can authenticate but cannot say whose
            // position it is, so refuse rather than upload an unattributed fix.
            call.reject("username is required to start location tracking");
            return;
        }
        Intent intent = new Intent(getContext(), LocationTrackingService.class);
        intent.putExtra("username", username.trim());
        String userId = call.getString("userId");
        if (userId != null) {
            intent.putExtra("userId", userId);
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                getContext().startForegroundService(intent);
            } else {
                getContext().startService(intent);
            }
            JSObject ret = new JSObject();
            ret.put("started", true);
            call.resolve(ret);
        } catch (Exception e) {
            call.reject(e.getMessage() != null ? e.getMessage() : "Could not start tracking", e);
        }
    }

    @PluginMethod
    public void stop(PluginCall call) {
        Intent intent = new Intent(getContext(), LocationTrackingService.class);
        intent.setAction("stop");
        try {
            getContext().startService(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject(e.getMessage() != null ? e.getMessage() : "Could not stop tracking", e);
        }
    }

    @PluginMethod
    public void isRunning(PluginCall call) {
        android.app.ActivityManager am =
                (android.app.ActivityManager) getContext().getSystemService(Context.ACTIVITY_SERVICE);
        boolean running = false;
        if (am != null) {
            // Cheap enough, and tells the UI the truth about whether background
            // tracking is actually alive rather than assuming it is.
            for (android.app.ActivityManager.RunningServiceInfo info : am.getRunningServices(100)) {
                if (info.service.getClassName().equals(LocationTrackingService.class.getName())) {
                    running = true;
                    break;
                }
            }
        }
        JSObject ret = new JSObject();
        ret.put("running", running);
        call.resolve(ret);
    }
}
