package com.jarvisos.app;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
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
        LocationTrackingService.setEnabled(getContext(), true);
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
        LocationTrackingService.setEnabled(getContext(), false);
        Intent intent = new Intent(getContext(), LocationTrackingService.class);
        intent.setAction("stop");
        try {
            getContext().startService(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject(e.getMessage() != null ? e.getMessage() : "Could not stop tracking", e);
        }
    }

    /**
     * Whether Android's battery optimisation leaves this app alone. While it
     * does not, Doze stops location and network for hours with the screen off
     * and the phone still -- the family map then shows someone "last seen"
     * the evening before.
     */
    @PluginMethod
    public void batteryUnrestricted(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("unrestricted", isUnrestricted());
        call.resolve(ret);
    }

    /** Ask Android (its own dialog) to exempt the app from battery optimisation. */
    @PluginMethod
    public void requestBatteryUnrestricted(PluginCall call) {
        if (isUnrestricted()) {
            JSObject ret = new JSObject();
            ret.put("unrestricted", true);
            call.resolve(ret);
            return;
        }
        try {
            Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
            intent.setData(Uri.parse("package:" + getContext().getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
        } catch (Exception e) {
            // Some builds hide that dialog: open the list instead.
            Intent list = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
            list.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(list);
        }
        JSObject ret = new JSObject();
        ret.put("unrestricted", false);
        call.resolve(ret);
    }

    /**
     * Whether location may be read with the app closed ("Allow all the time").
     *
     * The permission prompt only ever offers "While using the app" on Android
     * 11+, and with that grant the tracking service works only while it was
     * started from the open app. Once Android reclaims the process and the
     * service is restarted in the background it receives no fixes at all, so
     * the phone goes silent with no error (seen: a phone reporting every ten
     * minutes overnight, then nothing from 06:55 on).
     */
    @PluginMethod
    public void backgroundLocation(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("granted", LocationTrackingService.hasBackgroundLocation(getContext()));
        ret.put("foreground", getContext().checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED);
        call.resolve(ret);
    }

    /**
     * Ask for "Allow all the time". From Android 11 the system answers this
     * request by opening the app's location page, where that option lives;
     * on builds that do not, open the app's settings instead.
     */
    @PluginMethod
    public void requestBackgroundLocation(PluginCall call) {
        if (LocationTrackingService.hasBackgroundLocation(getContext())) {
            JSObject ret = new JSObject();
            ret.put("granted", true);
            call.resolve(ret);
            return;
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && getActivity() != null) {
                getActivity().requestPermissions(new String[]{Manifest.permission.ACCESS_BACKGROUND_LOCATION}, 0x10A2);
            } else {
                throw new IllegalStateException("no activity");
            }
        } catch (Exception e) {
            Intent details = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:" + getContext().getPackageName()));
            details.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(details);
        }
        JSObject ret = new JSObject();
        ret.put("granted", false);
        call.resolve(ret);
    }

    private boolean isUnrestricted() {
        PowerManager pm = (PowerManager) getContext().getSystemService(Context.POWER_SERVICE);
        return pm != null && pm.isIgnoringBatteryOptimizations(getContext().getPackageName());
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
