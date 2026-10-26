package com.jarvisos.app;

import android.Manifest;
import android.app.AlarmManager;
import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.ZoneId;

/**
 * Keeps the hardware step counter flowing to the server while the app is closed,
 * without requiring location sharing.
 *
 * Until this service, background step readings rode along with
 * {@link LocationTrackingService} uploads: switching location off also stopped
 * steps, and a phone whose app was not opened reported almost nothing. This
 * service owns only the pedometer, posts the same {@code /api/geo/steps}
 * payload the WebView sends, and is independent of location consent.
 */
public class StepSyncService extends Service {
    private static final String TAG = "StepSyncService";
    private static final String PREFS = "jarvis_steps";
    private static final String KEY_ENABLED = "sync_enabled";
    private static final String KEY_STEPS = "steps_enabled";
    private static final String ACTION_TICK = "com.jarvisos.app.STEP_SYNC_TICK";
    private static final long INTERVAL_MS = 15 * 60 * 1000L;
    private static final long WAKE_MS = 2 * 60 * 1000L;
    private static final int NOTIFICATION_ID = 0x10A2;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private SensorManager sensorManager;
    private Sensor stepSensor;
    private StepLedger ledger;
    private PowerManager.WakeLock wakeLock;
    private String username;
    private String userId;
    private volatile int todaySteps = -1;

    static void setEnabled(Context context, boolean enabled) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putBoolean(KEY_ENABLED, enabled).apply();
    }

    static boolean isEnabled(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getBoolean(KEY_ENABLED, false);
    }

    static void setStepsEnabled(Context context, boolean enabled) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putBoolean(KEY_STEPS, enabled).apply();
    }

    static boolean isStepsEnabled(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getBoolean(KEY_STEPS, true);
    }

    /** Put step sync back after a reboot or an app update. */
    static void resumeIfEnabled(Context context) {
        if (!isEnabled(context) || !isStepsEnabled(context)) return;
        Intent intent = new Intent(context, StepSyncService.class);
        intent.putExtra("resume", true);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "could not resume step sync", e);
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        sensorManager = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        stepSensor = sensorManager == null ? null : sensorManager.getDefaultSensor(Sensor.TYPE_STEP_COUNTER);
        ledger = new StepLedger(this);
        SharedPreferences p = TokenBridgePlugin.prefs(this);
        username = p.getString(TokenBridgePlugin.KEY_USERNAME, null);
        userId = p.getString(TokenBridgePlugin.KEY_USER_ID, null);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && "stop".equals(intent.getAction())) {
            stopSync();
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_TICK.equals(intent.getAction())) {
            startForegroundCompat();
            onTick();
            return START_STICKY;
        }
        if (intent != null) {
            String user = intent.getStringExtra("username");
            if (user != null && !user.trim().isEmpty()) {
                persistIdentity(user.trim(), intent.getStringExtra("userId"));
            }
        }
        setEnabled(this, true);
        startForegroundCompat();
        readSteps();
        uploadSteps();
        handler.postDelayed(this::uploadSteps, 12_000L);
        scheduleTick();
        return START_STICKY;
    }

    /**
     * Android 15 caps a `dataSync` foreground service at six hours in any
     * twenty-four, and stops it without asking. Stopping deliberately keeps the
     * notice honest and leaves the switches as the person set them; the tick
     * alarm and the next app open both re-arm this, and the remaining ticks of
     * the day run once the cap resets.
     */
    @Override
    public void onTimeout(int startId, int fgsType) {
        Log.w(TAG, "foreground time limit reached; stopping until the cap resets");
        stopSync();
    }

    @Override
    public void onTimeout(int startId) {
        Log.w(TAG, "foreground time limit reached; stopping until the cap resets");
        stopSync();
    }

    private void persistIdentity(String user, String id) {
        username = user;
        userId = id;
        TokenBridgePlugin.prefs(this).edit()
                .putString(TokenBridgePlugin.KEY_USERNAME, user)
                .putString(TokenBridgePlugin.KEY_USER_ID, id)
                .apply();
    }

    private void startForegroundCompat() {
        Notification notification = BackgroundNotice.build(
                this, PushNotifier.STATUS_ICON, "Step counting is on", null);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void acquireWakeLock() {
        if (wakeLock != null) return;
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:steps");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire(WAKE_MS);
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) {
            try {
                wakeLock.release();
            } catch (RuntimeException ignored) {
                // Already released by timeout.
            }
            wakeLock = null;
        }
    }

    private void scheduleTick() {
        AlarmManager am = (AlarmManager) getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        PendingIntent pi = tickIntent();
        long at = SystemClock.elapsedRealtime() + INTERVAL_MS;
        try {
            boolean exact = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms();
            if (exact) {
                am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
            } else {
                am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
            }
        } catch (SecurityException e) {
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
        }
    }

    private PendingIntent tickIntent() {
        Intent i = new Intent(this, StepSyncService.class).setAction(ACTION_TICK);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? PendingIntent.getForegroundService(this, 2, i, flags)
                : PendingIntent.getService(this, 2, i, flags);
    }

    private void cancelTick() {
        AlarmManager am = (AlarmManager) getSystemService(Context.ALARM_SERVICE);
        if (am != null) am.cancel(tickIntent());
    }

    private void onTick() {
        acquireWakeLock();
        readSteps();
        uploadSteps();
        handler.postDelayed(this::uploadSteps, 12_000L);
        scheduleTick();
        handler.postDelayed(this::releaseWakeLock, WAKE_MS);
    }

    /** Read the counter once; the sensor reports its cumulative value immediately. */
    private void readSteps() {
        if (!isStepsEnabled(this) || stepSensor == null || sensorManager == null) {
            todaySteps = ledger == null ? -1 : ledger.daySteps(LocalDate.now().toString(), StepLedger.SOURCE_PHONE);
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
                && checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        final SensorEventListener once = new SensorEventListener() {
            @Override
            public void onSensorChanged(SensorEvent event) {
                sensorManager.unregisterListener(this);
                try {
                    todaySteps = StepCounterPlugin.recordReading(StepSyncService.this, ledger, event.values[0]);
                } catch (RuntimeException e) {
                    Log.w(TAG, "step reading failed", e);
                }
            }

            @Override
            public void onAccuracyChanged(Sensor sensor, int accuracy) {
                // Not used.
            }
        };
        if (sensorManager.registerListener(once, stepSensor, SensorManager.SENSOR_DELAY_NORMAL)) {
            handler.postDelayed(() -> sensorManager.unregisterListener(once), 10_000L);
        }
    }

    private void uploadSteps() {
        SharedPreferences p = TokenBridgePlugin.prefs(this);
        String token = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
        String serverUrl = p.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
        if (token == null || serverUrl == null || token.isEmpty() || serverUrl.isEmpty()) return;
        if (username == null || username.isEmpty()) return;
        int steps = todaySteps;
        if (steps < 0) return;
        String base = serverUrl.endsWith("/") ? serverUrl.substring(0, serverUrl.length() - 1) : serverUrl;
        try {
            JSONObject body = new JSONObject();
            body.put("user_id", userId != null ? userId : username);
            if (userId != null) body.put("username", username);
            body.put("steps", steps);
            body.put("timestamp", System.currentTimeMillis() / 1000.0);
            body.put("source", StepLedger.SOURCE_PHONE);
            body.put("timezone", ZoneId.systemDefault().getId());
            int status = post(base + "/api/geo/steps", token, body);
            if (status >= 200 && status < 300) {
                Log.i(TAG, "uploaded " + steps + " steps (" + status + ")");
            } else {
                Log.w(TAG, "step upload failed HTTP " + status);
            }
        } catch (Exception e) {
            Log.e(TAG, "step upload threw", e);
        }
    }

    private int post(String url, String token, JSONObject body) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(10_000);
        conn.setReadTimeout(30_000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Authorization", "Bearer " + token);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(body.toString().getBytes(StandardCharsets.UTF_8));
        }
        int status = conn.getResponseCode();
        conn.disconnect();
        return status;
    }

    private void stopSync() {
        setEnabled(this, false);
        cancelTick();
        handler.removeCallbacksAndMessages(null);
        releaseWakeLock();
        stopForeground(true);
        stopSelf();
    }

    @Override
    public void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        releaseWakeLock();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
