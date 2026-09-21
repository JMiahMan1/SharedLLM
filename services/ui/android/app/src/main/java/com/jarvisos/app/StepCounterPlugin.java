package com.jarvisos.app;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Build;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import com.getcapacitor.PermissionState;

/**
 * Hardware pedometer bridge: exposes the phone's TYPE_STEP_COUNTER sensor to the
 * WebView app. The sensor reports the cumulative step count since last reboot;
 * we maintain a midnight baseline in SharedPreferences so the JS layer gets a
 * clean "steps taken today" value. Returns availability flags so the UI can
 * degrade gracefully on devices without the sensor (no fake data, ever).
 */
@CapacitorPlugin(
    name = "StepCounter",
    permissions = {
        @Permission(
            alias = "activityRecognition",
            strings = { Manifest.permission.ACTIVITY_RECOGNITION }
        )
    }
)
public class StepCounterPlugin extends Plugin implements SensorEventListener {

    private static final String PREFS = "step_counter";
    private static final String KEY_BASELINE = "midnight_baseline";
    private static final String KEY_BASELINE_DATE = "midnight_baseline_date";
    private static final String KEY_LAST_CUMULATIVE = "last_cumulative";
    private static final String KEY_HAS_BASELINE = "has_baseline";

    private SensorManager sensorManager;
    private Sensor stepSensor;
    private boolean listening = false;

    @Override
    public void load() {
        sensorManager = (SensorManager) bridge.getContext().getSystemService(Context.SENSOR_SERVICE);
        if (sensorManager != null) {
            stepSensor = sensorManager.getDefaultSensor(Sensor.TYPE_STEP_COUNTER);
        }
    }

    @PluginMethod
    public void isAvailable(PluginCall call) {
        JSObject ret = new JSObject();
        boolean hasSensor = stepSensor != null;
        ret.put("available", hasSensor);
        boolean needsPermission = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q;
        ret.put("permissionRequired", hasSensor && needsPermission);
        boolean granted = true;
        if (hasSensor && needsPermission) {
            granted = ContextCompat.checkSelfPermission(bridge.getContext(), Manifest.permission.ACTIVITY_RECOGNITION)
                == PackageManager.PERMISSION_GRANTED;
        }
        ret.put("permissionGranted", granted);
        call.resolve(ret);
    }

    @PluginMethod
    public void requestPermission(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            // Pre-Android-10: no runtime permission required for step counter
            JSObject ret = new JSObject();
            ret.put("granted", true);
            call.resolve(ret);
            return;
        }
        if (getPermissionState("activityRecognition") == PermissionState.GRANTED) {
            JSObject ret = new JSObject();
            ret.put("granted", true);
            call.resolve(ret);
            return;
        }
        requestPermissionForAlias("activityRecognition", call, "onPermissionResult");
    }

    @PermissionCallback
    private void onPermissionResult(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("granted", getPermissionState("activityRecognition") == PermissionState.GRANTED);
        call.resolve(ret);
    }

    @PluginMethod
    public void getTodaySteps(PluginCall call) {
        if (stepSensor == null) {
            call.resolve(new JSObject() {{
                put("available", false);
            }});
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
            && ContextCompat.checkSelfPermission(bridge.getContext(), Manifest.permission.ACTIVITY_RECOGNITION)
                != PackageManager.PERMISSION_GRANTED) {
            call.reject("ACTIVITY_RECOGNITION permission not granted");
            return;
        }

        // One-shot read: register briefly, capture the current counter value, stop.
        // TYPE_STEP_COUNTER always delivers its current value immediately on registration.
        SensorEventListener oneShot = new SensorEventListener() {
            @Override
            public void onSensorChanged(SensorEvent event) {
                try {
                    float cumulative = event.values[0];
                    int today = computeTodaySteps(cumulative);
                    JSObject ret = new JSObject();
                    ret.put("available", true);
                    ret.put("cumulativeSinceBoot", cumulative);
                    ret.put("steps", today);
                    call.resolve(ret);
                } catch (Exception e) {
                    call.reject("Failed to read step counter: " + e.getMessage());
                } finally {
                    sensorManager.unregisterListener(this);
                }
            }

            @Override
            public void onAccuracyChanged(Sensor sensor, int accuracy) {
            }
        };
        if (!sensorManager.registerListener(oneShot, stepSensor, SensorManager.SENSOR_DELAY_UI)) {
            call.reject("Failed to register step counter listener");
        }
    }

    @PluginMethod
    public void startPolling(PluginCall call) {
        if (stepSensor == null || listening) {
            call.resolve();
            return;
        }
        listening = sensorManager.registerListener(this, stepSensor, SensorManager.SENSOR_DELAY_UI);
        call.resolve();
    }

    @PluginMethod
    public void stopPolling(PluginCall call) {
        if (listening) {
            sensorManager.unregisterListener(this);
            listening = false;
        }
        call.resolve();
    }

    @Override
    public void onSensorChanged(SensorEvent event) {
        if (event.sensor.getType() != Sensor.TYPE_STEP_COUNTER) return;
        float cumulative = event.values[0];
        int today = computeTodaySteps(cumulative);
        JSObject ret = new JSObject();
        ret.put("available", true);
        ret.put("steps", today);
        ret.put("cumulativeSinceBoot", cumulative);
        notifyListeners("stepUpdate", ret);
    }

    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) {
    }

    /**
     * Convert the since-reboot cumulative counter into steps-today.
     * Baseline is captured at first read of each calendar day; if the counter
     * dropped below baseline (reboot), re-baseline and count from zero.
     */
    private int computeTodaySteps(float cumulative) {
        SharedPreferences prefs = bridge.getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String today = java.time.LocalDate.now().toString();
        String baselineDate = prefs.getString(KEY_BASELINE_DATE, "");
        boolean hasBaseline = prefs.getBoolean(KEY_HAS_BASELINE, false);
        long baseline = prefs.getLong(KEY_BASELINE, 0L);

        // Counter reset (reboot) invalidates the baseline
        if (hasBaseline && (long) cumulative < baseline) {
            hasBaseline = false;
        }

        if (!hasBaseline || !today.equals(baselineDate)) {
            // New day or first ever read: today's steps start from the current counter
            baseline = (long) cumulative;
            baselineDate = today;
            hasBaseline = true;
            SharedPreferences.Editor editor = prefs.edit();
            editor.putLong(KEY_BASELINE, baseline);
            editor.putString(KEY_BASELINE_DATE, baselineDate);
            editor.putBoolean(KEY_HAS_BASELINE, hasBaseline);
            editor.putLong(KEY_LAST_CUMULATIVE, (long) cumulative);
            editor.apply();
            return 0;
        }

        int todaySteps = (int) Math.max(0, (long) cumulative - baseline);
        SharedPreferences.Editor editor = prefs.edit();
        editor.putLong(KEY_LAST_CUMULATIVE, (long) cumulative);
        editor.apply();
        return todaySteps;
    }
}
