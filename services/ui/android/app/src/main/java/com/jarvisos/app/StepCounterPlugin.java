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
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSArray;
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
    private static final String KEY_LAST_READ_AT = "last_read_at";
    /**
     * Only credit the across-midnight delta to the new day when the previous
     * reading was this recent. A longer gap can contain an entire unrecorded
     * day, and attributing it to today would inflate today's count.
     */
    private static final long MIDNIGHT_CREDIT_WINDOW_MS = 60L * 60L * 1000L;

    private SensorManager sensorManager;
    private Sensor stepSensor;
    private boolean listening = false;
    private StepLedger ledger;

    @Override
    public void load() {
        ledger = new StepLedger(bridge.getContext());
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
        PermissionState state = getPermissionState("activityRecognition");
        if (state == PermissionState.GRANTED) {
            JSObject ret = new JSObject();
            ret.put("granted", true);
            call.resolve(ret);
            return;
        }
        if (state == PermissionState.DENIED && isPermanentlyDenied()) {
            // System will not show the dialog again — UI must deep-link to Settings.
            JSObject ret = new JSObject();
            ret.put("granted", false);
            ret.put("permanentlyDenied", true);
            call.resolve(ret);
            return;
        }
        requestPermissionForAlias("activityRecognition", call, "onPermissionResult");
    }

    /**
     * Capacitor's PermissionState has no DENIED_WITH_ALWAYS; Android signals a
     * permanent deny as DENIED + shouldShowRequestPermissionRationale == false.
     */
    private boolean isPermanentlyDenied() {
        try {
            android.app.Activity activity = getActivity();
            if (activity == null) {
                return false;
            }
            return !ActivityCompat.shouldShowRequestPermissionRationale(
                activity, Manifest.permission.ACTIVITY_RECOGNITION);
        } catch (Exception e) {
            return false;
        }
    }

    @PermissionCallback
    private void onPermissionResult(PluginCall call) {
        PermissionState state = getPermissionState("activityRecognition");
        boolean granted = state == PermissionState.GRANTED;
        // If this permission was requested so polling could start, attach the
        // sensor listener now — otherwise steps stay at 0 until the next open.
        if (granted && stepSensor != null && !listening) {
            listening = sensorManager.registerListener(this, stepSensor, SensorManager.SENSOR_DELAY_UI);
        }
        JSObject ret = new JSObject();
        ret.put("granted", granted);
        ret.put("permanentlyDenied", !granted && isPermanentlyDenied());
        call.resolve(ret);
    }

    @PluginMethod
    public void openSettings(PluginCall call) {
        try {
            android.content.Intent intent = new android.content.Intent(
                android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                android.net.Uri.parse("package:" + bridge.getContext().getPackageName())
            );
            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            bridge.getContext().startActivity(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject("Failed to open app settings: " + e.getMessage());
        }
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
            // Explicit reject (not a silent empty read) so the JS layer can
            // surface "permission denied" and disable the feature in Settings.
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
        } else {
            // Fail loudly if the sensor never delivers (permission revoked mid-read,
            // OEM sensor lock, etc.) instead of hanging the JS promise forever.
            final PluginCall pending = call;
            android.os.Handler timeout = new android.os.Handler(android.os.Looper.getMainLooper());
            timeout.postDelayed(() -> {
                try {
                    sensorManager.unregisterListener(oneShot);
                    pending.reject("Step counter sensor did not deliver a reading");
                } catch (Exception ignored) {
                    // call already resolved
                }
            }, 5000);
        }
    }

    @PluginMethod
    public void startPolling(PluginCall call) {
        if (stepSensor == null) {
            call.resolve();
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
            && ContextCompat.checkSelfPermission(bridge.getContext(), Manifest.permission.ACTIVITY_RECOGNITION)
                != PackageManager.PERMISSION_GRANTED) {
            call.reject("ACTIVITY_RECOGNITION permission not granted");
            return;
        }
        // Always re-register: a prior register without ACTIVITY_RECOGNITION can
        // leave `listening=true` while delivering zero events. Unregister first
        // so a retry after permission grant actually attaches the listener.
        if (listening) {
            sensorManager.unregisterListener(this);
            listening = false;
        }
        listening = sensorManager.registerListener(this, stepSensor, SensorManager.SENSOR_DELAY_UI);
        if (!listening) {
            call.reject("Failed to attach step counter listener");
            return;
        }
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
     *
     * Deltas from the cumulative sensor are accumulated into a per-day total so
     * steps keep logging even when readings arrive before the app is opened.
     * The day bucket rolls over at local midnight (date change on any read or
     * sensor event); a reboot (counter drops) re-baselines without double-counting.
     */
    /**
     * Ledger history for client backfill. After a gap (app not opened for a
     * day), the ledger still has those days, so the client can reconcile the
     * server instead of losing them.
     */
    @PluginMethod
    public void getDayHistory(PluginCall call) {
        Integer days = call.getInt("days", 30);
        JSArray out = new JSArray();
        for (StepLedger.DayEntry entry : ledger.history(days == null ? 30 : days, StepLedger.SOURCE_PHONE)) {
            JSObject row = new JSObject();
            row.put("day", entry.day);
            row.put("steps", entry.steps);
            row.put("source", entry.source);
            row.put("updatedAt", entry.updatedAt / 1000.0);
            out.put(row);
        }
        JSObject ret = new JSObject();
        ret.put("days", out);
        ret.put("source", StepLedger.SOURCE_PHONE);
        call.resolve(ret);
    }

    /** Days recorded after `since` (exclusive), oldest first, for backfill. */
    @PluginMethod
    public void getDaysSince(PluginCall call) {
        String since = call.getString("since");
        Integer max = call.getInt("max", 60);
        JSArray out = new JSArray();
        for (StepLedger.DayEntry entry : ledger.daysSince(since, max == null ? 60 : max)) {
            JSObject row = new JSObject();
            row.put("day", entry.day);
            row.put("steps", entry.steps);
            row.put("source", entry.source);
            row.put("updatedAt", entry.updatedAt / 1000.0);
            out.put(row);
        }
        JSObject ret = new JSObject();
        ret.put("days", out);
        ret.put("source", StepLedger.SOURCE_PHONE);
        call.resolve(ret);
    }

    private int computeTodaySteps(float cumulative) {
        SharedPreferences prefs = bridge.getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String today = java.time.LocalDate.now().toString();
        String baselineDate = prefs.getString(KEY_BASELINE_DATE, "");
        boolean hasBaseline = prefs.getBoolean(KEY_HAS_BASELINE, false);
        long baseline = prefs.getLong(KEY_BASELINE, 0L);
        long daySteps = prefs.getLong("day_steps", 0L);
        long lastReadAt = prefs.getLong(KEY_LAST_READ_AT, 0L);
        long now = System.currentTimeMillis();

        // Counter reset (reboot) invalidates the baseline; keep the day's total.
        if (hasBaseline && (long) cumulative < baseline) {
            hasBaseline = false;
        }

        if (!hasBaseline || !today.equals(baselineDate)) {
            // Midnight rollover. The sensor only gives a cumulative count, so we
            // can credit the delta to the new day — but ONLY when the gap is
            // short (the classic case: a reading just before midnight and one
            // just after, e.g. while asleep). Across a whole day with no reads,
            // that delta contains an entire lost day of walking; attributing it
            // to today would inflate today's count, which is exactly the bug
            // users saw after the app went a day without syncing.
            boolean recentReading = lastReadAt > 0 && (now - lastReadAt) <= MIDNIGHT_CREDIT_WINDOW_MS;
            if (hasBaseline && !today.equals(baselineDate) && recentReading && (long) cumulative >= baseline) {
                daySteps += (long) cumulative - baseline;
            } else {
                daySteps = 0;
            }
            baseline = (long) cumulative;
            baselineDate = today;
            hasBaseline = true;
            SharedPreferences.Editor editor = prefs.edit();
            editor.putLong(KEY_BASELINE, baseline);
            editor.putString(KEY_BASELINE_DATE, baselineDate);
            editor.putBoolean(KEY_HAS_BASELINE, hasBaseline);
            editor.putLong("day_steps", daySteps);
            editor.putLong(KEY_LAST_CUMULATIVE, (long) cumulative);
            editor.putLong(KEY_LAST_READ_AT, now);
            editor.apply();
            // Persist the day rollup: history survives reboots and app kills.
            ledger.recordDay(today, (int) Math.min(Integer.MAX_VALUE, Math.max(0, daySteps)), StepLedger.SOURCE_PHONE);
            return (int) Math.min(Integer.MAX_VALUE, Math.max(0, daySteps));
        }

        long delta = (long) cumulative - baseline;
        if (delta < 0) {
            delta = 0;
        }
        daySteps += delta;
        baseline = (long) cumulative;
        SharedPreferences.Editor editor = prefs.edit();
        editor.putLong(KEY_BASELINE, baseline);
        editor.putLong("day_steps", daySteps);
        editor.putLong(KEY_LAST_CUMULATIVE, (long) cumulative);
        editor.putLong(KEY_LAST_READ_AT, now);
        editor.apply();
        ledger.recordDay(today, (int) Math.min(Integer.MAX_VALUE, Math.max(0, daySteps)), StepLedger.SOURCE_PHONE);
        return (int) Math.min(Integer.MAX_VALUE, Math.max(0, daySteps));
    }
}
