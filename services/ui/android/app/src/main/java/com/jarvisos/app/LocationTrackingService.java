package com.jarvisos.app;

import android.Manifest;
import android.app.AlarmManager;
import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;
import androidx.core.content.ContextCompat;
import com.google.android.gms.common.ConnectionResult;
import com.google.android.gms.common.GoogleApiAvailability;
import com.google.android.gms.location.ActivityRecognition;
import com.google.android.gms.location.ActivityRecognitionClient;
import com.google.android.gms.location.ActivityTransition;
import com.google.android.gms.location.ActivityTransitionRequest;
import com.google.android.gms.location.DetectedActivity;
import com.google.android.gms.location.FusedLocationProviderClient;
import com.google.android.gms.location.LocationCallback;
import com.google.android.gms.location.LocationRequest;
import com.google.android.gms.location.LocationResult;
import com.google.android.gms.location.LocationServices;
import com.google.android.gms.location.Priority;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;

/**
 * Uploads this device's location while the app is closed.
 *
 * The WebView's {@code Geolocation.watchPosition} only lives as long as the JS
 * context does. On an aggressive OEM build the process gets reclaimed once the
 * app is backgrounded, and the watch dies with it -- which is why location *and*
 * steps both stopped at the same instant in production, rather than location
 * alone as a permission problem would cause. A foreground service is the only
 * construct Android lets keep this running once the app is not in front.
 *
 * Credentials come from TokenBridge's prefs, which the login flow already
 * mirrors natively, so this works with no WebView at all -- including after the
 * OS restarts the service.
 *
 * A fix that cannot be sent (no signal, the server restarting) is kept in
 * {@link LocationOutbox} and sent, oldest first, ahead of the next live fix.
 *
 * Fixes come from Google Play services' fused provider when the phone has it,
 * at a rate set by what the person is doing ({@link MotionModeSelector}): once
 * a second while driving, every 15 s on foot or a bike, Wi-Fi and cell once a
 * minute while still. Asking GPS and network separately (the fallback below,
 * for phones without Play services) uploaded whichever fix won, so 80 m
 * network fixes with no speed interleaved with 5 m GPS ones and a phone at 36
 * mph was reported standing still. While driving, {@link DrivingMonitor}
 * notices hard braking, rapid acceleration, high speed and phone use, and
 * {@link CrashDetector} listens to the accelerometer for a crash.
 */
public class LocationTrackingService extends Service {
    private static final String TAG = "LocationTracking";
    private static final int NOTIFICATION_ID = 0x10A1;
    /** The LocationManager fallback's rate, unchanged from before the fused provider. */
    private static final long MIN_TIME_MS = 30_000L;
    private static final float MIN_DISTANCE_M = 5f;
    /**
     * Ask for a fresh fix this often even when nothing moved. Without it a
     * stationary device produces no LocationManager callback at all, so the
     * family's view of "still there" silently ages out. (This was declared
     * once and never scheduled; it was also used as the upload throttle, so a
     * moving phone uploaded at most every five minutes.)
     */
    private static final long HEARTBEAT_MS = 5 * 60 * 1000L;
    private static final String ACTION_HEARTBEAT = "heartbeat";
    /** Held through one heartbeat: the fresh fix plus its upload. */
    private static final long HEARTBEAT_WAKE_MS = 90_000L;
    /** A fix this much newer than the one we hold replaces it regardless. */
    private static final long SIGNIFICANTLY_NEWER_MS = 2 * 60 * 1000L;

    private LocationManager locationManager;
    private PowerManager.WakeLock wakeLock;
    private volatile Location lastKnown;
    private volatile long lastUploadAt = 0L;
    /**
     * Plugging in, unplugging and a new next alarm each send a report of their
     * own (automations trigger on them), rate-limited by {@link PhoneStatusRules.Limiter}.
     */
    private final PhoneStatusRules.Limiter statusLimiter = new PhoneStatusRules.Limiter();
    private boolean statusReceiverRegistered = false;
    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            Log.i(TAG, "status change: " + intent.getAction());
            requestStatusReport();
        }
    };
    /** Fix time of the last fix uploaded, so an unchanged fix is not re-sent. */
    private volatile long lastUploadedFixTime = 0L;
    private volatile String username;
    private volatile String userId;
    private volatile boolean uploading = false;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private volatile boolean updatesRequested = false;

    /** Null without Google Play services: the LocationManager code below is used instead. */
    private FusedLocationProviderClient fused;
    /** The mode the current fused request was made for, so a change re-requests. */
    private MotionModeSelector.Mode requestedMode;
    private ActivityRecognitionClient activityClient;
    private boolean transitionsRegistered = false;
    /** Read and changed on the main thread only (fixes and transitions arrive there). */
    private MotionModeSelector motion;
    private final DrivingMonitor drivingMonitor = new DrivingMonitor();
    /** Main thread only, like {@link #motion}: accelerometer samples are delivered there too. */
    private final CrashDetector crashDetector = new CrashDetector();
    private SensorManager sensorManager;
    private boolean accelRegistered = false;
    /**
     * Held while the accelerometer listens. Without it the CPU sleeps between
     * once-a-second fixes with the screen off, and an impact in between is
     * never delivered. Bounded, and renewed with each fix while driving.
     */
    private PowerManager.WakeLock crashWakeLock;
    private static final long CRASH_WAKE_MS = 5 * 60 * 1000L;

    private final SensorEventListener accelListener = new SensorEventListener() {
        @Override
        public void onSensorChanged(SensorEvent event) {
            boolean wasPending = crashDetector.isPending();
            CrashDetector.Result crash = crashDetector.onAccel(wallTime(event.timestamp),
                    event.values[0], event.values[1], event.values[2]);
            if (crash != null) CrashAlert.start(LocationTrackingService.this, crash);
            reportIgnoredHit();
            if (wasPending != crashDetector.isPending()) syncCrashListening();
        }

        @Override
        public void onAccuracyChanged(Sensor sensor, int accuracy) {
            // Not used.
        }
    };
    /** The running service, for transitions the receiver hands over. */
    private static volatile LocationTrackingService instance;

    private final LocationCallback fusedCallback = new LocationCallback() {
        @Override
        public void onLocationResult(LocationResult result) {
            for (Location location : result.getLocations()) consider(location);
        }
    };

    private final LocationListener listener = new LocationListener() {
        @Override
        public void onLocationChanged(Location location) {
            consider(location);
        }

        @Override
        public void onProviderDisabled(String provider) {
            Log.w(TAG, "provider disabled: " + provider);
        }

        @Override
        public void onProviderEnabled(String provider) {
            Log.i(TAG, "provider enabled: " + provider);
        }

        @Override
        public void onStatusChanged(String provider, int status, Bundle extras) {
            // Deprecated and never called on modern Android.
        }
    };

    private static final String PREFS = "jarvis_location";
    private static final String KEY_ENABLED = "tracking_enabled";
    private static final String KEY_STEPS = "steps_enabled";
    /** The current mode and its last moving fix, so a restarted service resumes a drive. */
    private static final String KEY_MODE = "motion_mode";
    private static final String KEY_MOVING_AT = "motion_moving_at";
    static final String ACTION_ACTIVITY_ACCESS = "activity_access";
    /** A step reading is attached to a fix only while both are this fresh. */
    private static final long STEPS_FRESH_MS = 10 * 60 * 1000L;

    private StepLedger stepLedger;
    private volatile int todaySteps = -1;
    private volatile long todayStepsAt = 0L;
    /** Local day (yyyy-MM-dd) the reading belongs to. */
    private volatile String todayStepsDay = "";

    /** Whether the user has step counting on; the app passes it at start. */
    static void setStepsEnabled(Context context, boolean enabled) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_STEPS, enabled).apply();
    }

    /** Remembered so tracking can resume after a reboot or an app update. */
    static void setEnabled(Context context, boolean enabled) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, enabled).apply();
    }

    /**
     * Only a deliberate "on" counts. Builds before this flag never wrote it,
     * and treating a missing flag as on would also resume sharing for someone
     * who had switched it off there; the app records it when next opened.
     */
    static boolean isEnabled(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_ENABLED, false);
    }

    /**
     * Restart tracking after a reboot or update, if it was on. Without
     * "Allow all the time" a service started now would get no fixes, so it is
     * not started; the app says so when next opened.
     */
    static void resumeIfEnabled(Context context) {
        if (!isEnabled(context)) return;
        if (!hasBackgroundLocation(context)) {
            Log.w(TAG, "tracking was on but location is 'while using the app' only; not resuming in background");
            return;
        }
        Intent intent = new Intent(context, LocationTrackingService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    /** "Allow all the time": without it a service started in the background gets no fixes. */
    static boolean hasBackgroundLocation(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                    == PackageManager.PERMISSION_GRANTED;
        }
        return context.checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
    }

    /**
     * Activity Recognition may run: a runtime permission from Android 10, and
     * before that Play services' own install-time one (in the manifest).
     */
    static boolean hasActivityRecognition(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return true;
        return context.checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION)
                == PackageManager.PERMISSION_GRANTED;
    }

    static boolean hasPlayServices(Context context) {
        try {
            return GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(context)
                    == ConnectionResult.SUCCESS;
        } catch (RuntimeException e) {
            return false;
        }
    }

    private static MotionModeSelector loadMotion(Context context) {
        android.content.SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return new MotionModeSelector(MotionModeSelector.Mode.parse(p.getString(KEY_MODE, null)),
                p.getLong(KEY_MOVING_AT, 0L));
    }

    private static void saveMotion(Context context, MotionModeSelector motion) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(KEY_MODE, motion.mode().name())
                .putLong(KEY_MOVING_AT, motion.lastMovingAt())
                .apply();
    }

    /** Activity Recognition's activity type as a mode, or null for one not asked for. */
    static MotionModeSelector.Mode modeFor(int activityType) {
        switch (activityType) {
            case DetectedActivity.IN_VEHICLE:
                return MotionModeSelector.Mode.DRIVING;
            case DetectedActivity.ON_BICYCLE:
                return MotionModeSelector.Mode.CYCLING;
            case DetectedActivity.WALKING:
            case DetectedActivity.RUNNING:
                return MotionModeSelector.Mode.WALKING;
            case DetectedActivity.STILL:
                return MotionModeSelector.Mode.STILL;
            default:
                return null;
        }
    }

    /**
     * A transition from {@link ActivityTransitionReceiver}. The running service
     * takes it on its own thread; with none running it is only remembered, and
     * a service started later begins in that mode.
     */
    static void onActivityTransition(Context context, int activityType, boolean enter) {
        final MotionModeSelector.Mode mode = modeFor(activityType);
        if (mode == null) return;
        final LocationTrackingService running = instance;
        if (running != null) {
            running.handler.post(() -> running.applyTransition(mode, enter));
            return;
        }
        MotionModeSelector motion = loadMotion(context);
        if (motion.onTransition(mode, enter, System.currentTimeMillis())) saveMotion(context, motion);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        locationManager = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        motion = loadMotion(this);
        sensorManager = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        // A crash countdown or SOS retry the process was killed in the middle of.
        CrashAlert.resume(this);
        // The room-detection beacon lives as long as this process; a sticky restart brings it back.
        BleBeacon.ensure(this);
        if (hasPlayServices(this)) {
            fused = LocationServices.getFusedLocationProviderClient(this);
            activityClient = ActivityRecognition.getClient(this);
        }
        // Mirrored natively at login, so the service authenticates with no WebView.
        android.content.SharedPreferences p = TokenBridgePlugin.prefs(this);
        username = p.getString(TokenBridgePlugin.KEY_USERNAME, null);
        userId = p.getString(TokenBridgePlugin.KEY_USER_ID, null);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && intent.getAction() != null) {
            if ("stop".equals(intent.getAction())) {
                stopTracking();
                return START_NOT_STICKY;
            }
            if (ACTION_HEARTBEAT.equals(intent.getAction())) {
                startForegroundCompat();
                onHeartbeat();
                return START_STICKY;
            }
            if (ACTION_ACTIVITY_ACCESS.equals(intent.getAction()) && updatesRequested) {
                // Activity Recognition was just allowed in the app. A service
                // that was not running starts in full below instead.
                startForegroundCompat();
                registerTransitions();
                return START_STICKY;
            }
            if (intent.getStringExtra("username") != null) {
                persistIdentity(intent.getStringExtra("username"), intent.getStringExtra("userId"));
            }
        }
        setEnabled(this, true);
        startForegroundCompat();
        acquireWakeLock();
        readSteps();
        requestUpdates();
        registerTransitions();
        registerStatusReceiver();
        scheduleHeartbeat();
        // Sticky: if Android reclaims us, come back on our own.
        return START_STICKY;
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
                this, PushNotifier.STATUS_ICON, "Location sharing is on", null);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void acquireWakeLock() {
        if (wakeLock != null) return;
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        // Bounded: a foreground service already keeps us resident, so this only
        // needs to cover the gap where the device is dozing between fixes.
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:location");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire(10 * 60 * 1000L);
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

    private void requestUpdates() {
        if (fused == null && locationManager == null) return;
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "fine location not granted; cannot track");
            return;
        }
        try {
            if (fused != null) {
                requestFused();
            } else {
                locationManager.removeUpdates(listener);
                if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                    locationManager.requestLocationUpdates(
                            LocationManager.GPS_PROVIDER, MIN_TIME_MS, MIN_DISTANCE_M, listener);
                }
                if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                    locationManager.requestLocationUpdates(
                            LocationManager.NETWORK_PROVIDER, MIN_TIME_MS, MIN_DISTANCE_M, listener);
                }
            }
            updatesRequested = true;
            // A service restarted mid-drive listens for a crash straight away.
            crashDetector.setDriving(motion.mode() == MotionModeSelector.Mode.DRIVING);
            syncCrashListening();
            // Seed from the freshest cached fix so the family map is not blank
            // for the first interval after the service starts.
            Location cached = locationManager != null ? bestLastKnown() : null;
            if (cached != null) {
                lastKnown = cached;
                upload(cached, true);  // sent with its own (possibly old) fix time
            }
            Log.i(TAG, "tracking started for " + username);
        } catch (SecurityException e) {
            Log.e(TAG, "requestLocationUpdates denied", e);
        }
    }

    /** One fused request at the current mode's rate, replacing any earlier one. */
    private void requestFused() throws SecurityException {
        MotionModeSelector.Mode mode = requestMode();
        LocationRequest request = new LocationRequest.Builder(
                mode.highAccuracy ? Priority.PRIORITY_HIGH_ACCURACY : Priority.PRIORITY_BALANCED_POWER_ACCURACY,
                mode.intervalMs)
                .setMinUpdateDistanceMeters(mode.minDistanceM)
                .build();
        fused.removeLocationUpdates(fusedCallback);
        fused.requestLocationUpdates(request, fusedCallback, Looper.getMainLooper());
        requestedMode = mode;
        Log.i(TAG, "fused updates: " + mode.activity + " every " + mode.intervalMs + " ms");
    }

    /**
     * Ask Play services for enter and exit of driving, cycling, walking,
     * running and still. Without the permission (or Play services) the mode
     * comes from speed alone, so a mode only Activity Recognition could have
     * set is dropped: still asks for no GPS and so could never be left.
     */
    private void registerTransitions() {
        if (activityClient == null || !hasActivityRecognition(this)) {
            transitionsRegistered = false;
            MotionModeSelector.Mode before = motion.mode();
            if (motion.dropActivityRecognition()) onModeChanged(before);
            return;
        }
        if (transitionsRegistered) return;
        List<ActivityTransition> transitions = new ArrayList<>();
        for (int type : new int[]{DetectedActivity.IN_VEHICLE, DetectedActivity.ON_BICYCLE,
                DetectedActivity.WALKING, DetectedActivity.RUNNING, DetectedActivity.STILL}) {
            for (int transition : new int[]{ActivityTransition.ACTIVITY_TRANSITION_ENTER,
                    ActivityTransition.ACTIVITY_TRANSITION_EXIT}) {
                transitions.add(new ActivityTransition.Builder()
                        .setActivityType(type).setActivityTransition(transition).build());
            }
        }
        try {
            activityClient.requestActivityTransitionUpdates(
                            new ActivityTransitionRequest(transitions), transitionIntent())
                    .addOnSuccessListener(v -> {
                        transitionsRegistered = true;
                        Log.i(TAG, "activity transitions registered");
                    })
                    .addOnFailureListener(e -> {
                        transitionsRegistered = false;
                        Log.w(TAG, "activity transitions refused", e);
                        MotionModeSelector.Mode before = motion.mode();
                        if (motion.dropActivityRecognition()) onModeChanged(before);
                    });
        } catch (SecurityException e) {
            Log.w(TAG, "activity recognition denied", e);
        }
    }

    /** Mutable: Play services writes the transition result into it. */
    private PendingIntent transitionIntent() {
        Intent i = new Intent(this, ActivityTransitionReceiver.class);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT
                | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ? PendingIntent.FLAG_MUTABLE : 0);
        return PendingIntent.getBroadcast(this, 2, i, flags);
    }

    private void removeTransitions() {
        if (activityClient == null) return;
        try {
            activityClient.removeActivityTransitionUpdates(transitionIntent());
        } catch (RuntimeException ignored) {
            // Not registered, or the permission was taken back: nothing to remove.
        }
        transitionsRegistered = false;
    }

    private void applyTransition(MotionModeSelector.Mode mode, boolean enter) {
        MotionModeSelector.Mode before = motion.mode();
        if (motion.onTransition(mode, enter, System.currentTimeMillis())) onModeChanged(before);
    }

    /**
     * The mode changed: remember it, close a drive that ended, and ask for
     * fixes at the new rate.
     */
    private void onModeChanged(MotionModeSelector.Mode before) {
        MotionModeSelector.Mode now = motion.mode();
        Log.i(TAG, "mode " + before.activity + " -> " + now.activity);
        saveMotion(this, motion);
        if (before == MotionModeSelector.Mode.DRIVING && now != MotionModeSelector.Mode.DRIVING) {
            postDrivingEvents(drivingMonitor.finish());
        }
        crashDetector.setDriving(now == MotionModeSelector.Mode.DRIVING);
        syncCrashListening();
    }

    /**
     * The rate fixes are asked for: the mode's, except that a crash waiting
     * for its stop to be confirmed keeps driving's once a second. Activity
     * Recognition can call a crashed car "still" within the minute, and a
     * fix a minute later is too late to confirm anything.
     */
    private MotionModeSelector.Mode requestMode() {
        return crashDetector.isPending() ? MotionModeSelector.Mode.DRIVING : motion.mode();
    }

    /**
     * The accelerometer listens only while driving (or a crash is being
     * confirmed): at 50 samples a second it costs battery for nothing on foot.
     * Also re-asks for fixes when the rate they should come at changed.
     */
    private void syncCrashListening() {
        boolean want = updatesRequested && requestMode() == MotionModeSelector.Mode.DRIVING;
        if (want && !accelRegistered && sensorManager != null) {
            Sensor accel = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
            if (accel != null) {
                accelRegistered = sensorManager.registerListener(
                        accelListener, accel, SensorManager.SENSOR_DELAY_GAME, handler);
                if (accelRegistered) Log.i(TAG, "crash detection listening");
            }
        } else if (!want && accelRegistered) {
            stopCrashListening();
        }
        if (want) holdCrashWakeLock();
        if (fused != null && updatesRequested && requestedMode != requestMode()) {
            try {
                requestFused();
            } catch (SecurityException e) {
                Log.w(TAG, "fused re-request denied", e);
            }
        }
    }

    private void stopCrashListening() {
        if (accelRegistered && sensorManager != null) sensorManager.unregisterListener(accelListener);
        accelRegistered = false;
        if (crashWakeLock != null && crashWakeLock.isHeld()) {
            try {
                crashWakeLock.release();
            } catch (RuntimeException ignored) {
                // Already released by timeout.
            }
        }
    }

    private void holdCrashWakeLock() {
        if (!accelRegistered) return;
        if (crashWakeLock == null) {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm == null) return;
            crashWakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:crash");
            crashWakeLock.setReferenceCounted(false);
        }
        // Re-acquiring restarts the timeout: a drive with fixes keeps it, a
        // service that stops hearing anything lets it lapse.
        crashWakeLock.acquire(CRASH_WAKE_MS);
    }

    /**
     * Sensor time (nanoseconds since boot) as wall-clock milliseconds, so
     * accelerometer samples and fixes are on one clock. A fix's own time
     * comes from GPS and can differ from the phone's clock by seconds.
     */
    private static long wallTime(long elapsedNanos) {
        return System.currentTimeMillis() - (SystemClock.elapsedRealtimeNanos() - elapsedNanos) / 1_000_000L;
    }

    /** The detector's view of a fix: unwrapped so the rules stay free of Android types. */
    private void feedCrashDetector(Location location, double speed, double accuracy) {
        long at = location.getElapsedRealtimeNanos() > 0
                ? wallTime(location.getElapsedRealtimeNanos()) : location.getTime();
        boolean wasPending = crashDetector.isPending();
        crashDetector.setDriving(motion.mode() == MotionModeSelector.Mode.DRIVING);
        CrashDetector.Result crash = crashDetector.onFix(at, speed, accuracy,
                location.getLatitude(), location.getLongitude());
        if (crash != null) CrashAlert.start(this, crash);
        reportIgnoredHit();
        if (wasPending != crashDetector.isPending() || accelRegistered) syncCrashListening();
    }

    /** At most one ignored hit reported per this long: a rough road can jolt every few seconds. */
    private static final long IGNORED_REPORT_GAP_MS = 2 * 60 * 1000L;
    private long lastIgnoredReportAt = Long.MIN_VALUE / 2;

    /** A hard hit the detector let go (a drop, the car driving on), for the false-alarm record. */
    private void reportIgnoredHit() {
        JSONObject ignored = crashDetector.takeIgnored();
        if (ignored == null) return;
        long now = System.currentTimeMillis();
        if (now - lastIgnoredReportAt < IGNORED_REPORT_GAP_MS) return;
        lastIgnoredReportAt = now;
        CrashAlert.reportIgnored(this, ignored);
    }

    private void postDrivingEvents(List<DrivingMonitor.Event> events) {
        for (DrivingMonitor.Event event : events) {
            try {
                ActivityOutbox.add(this, event.toBody());
                Log.i(TAG, "driving event " + event.type);
            } catch (Exception e) {
                Log.w(TAG, "driving event not kept", e);
            }
        }
    }

    private boolean screenInteractive() {
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        return pm != null && pm.isInteractive();
    }

    private Location bestLastKnown() {
        Location best = null;
        try {
            for (String provider : new String[]{
                    LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
                if (!locationManager.isProviderEnabled(provider)) continue;
                Location l = locationManager.getLastKnownLocation(provider);
                if (l == null) continue;
                if (best == null || l.getTime() > best.getTime()) best = l;
            }
        } catch (SecurityException e) {
            Log.w(TAG, "getLastKnownLocation denied", e);
        }
        return best;
    }

    /**
     * Keep the better of a new fix and the one held, then upload when due.
     * GPS and network fixes both arrive here; without the comparison a coarse
     * network fix (tens of metres) that happened to arrive first was uploaded
     * and a precise GPS fix seconds later was dropped by the throttle.
     */
    private void consider(Location location) {
        double speed = location.hasSpeed() ? location.getSpeed() : Double.NaN;
        double accuracy = location.hasAccuracy() ? location.getAccuracy() : Double.NaN;
        MotionModeSelector.Mode before = motion.mode();
        boolean changed = motion.onFix(location.getTime(), speed, accuracy);
        if (motion.mode() == MotionModeSelector.Mode.DRIVING) {
            postDrivingEvents(drivingMonitor.onFix(location.getTime(), speed, accuracy,
                    location.getLatitude(), location.getLongitude(), screenInteractive()));
        }
        if (changed) onModeChanged(before);
        feedCrashDetector(location, speed, accuracy);
        if (isBetter(location, lastKnown)) lastKnown = location;
        if (System.currentTimeMillis() - lastUploadAt >= motion.mode().uploadEveryMs && lastKnown != null) {
            upload(lastKnown, false);
        }
    }

    /** Android's "is this a better fix" rule: newer, then more accurate. */
    static boolean isBetter(Location candidate, Location current) {
        if (current == null) return true;
        long dt = candidate.getTime() - current.getTime();
        if (dt > SIGNIFICANTLY_NEWER_MS) return true;
        if (dt < -SIGNIFICANTLY_NEWER_MS) return false;
        float da = (candidate.hasAccuracy() ? candidate.getAccuracy() : Float.MAX_VALUE)
                - (current.hasAccuracy() ? current.getAccuracy() : Float.MAX_VALUE);
        if (da < 0) return true;                       // more accurate
        if (dt > 0 && da == 0) return true;            // as accurate, newer
        boolean sameProvider = candidate.getProvider() != null
                && candidate.getProvider().equals(current.getProvider());
        return dt > 0 && da <= 200 && sameProvider;    // newer and not much worse
    }

    /**
     * The heartbeat runs off AlarmManager, not a Handler. A Handler delay
     * counts uptime, which stops while the CPU sleeps -- and with the screen
     * off and the phone still, it sleeps: the wake lock above lapses after ten
     * minutes and Doze ignores wake locks anyway. So a phone left on the
     * nightstand stopped reporting at all (seen: both phones silent for hours
     * overnight). An allow-while-idle alarm is delivered in Doze too.
     */
    private void scheduleHeartbeat() {
        AlarmManager am = (AlarmManager) getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        PendingIntent pi = heartbeatIntent();
        long at = SystemClock.elapsedRealtime() + HEARTBEAT_MS;
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

    private PendingIntent heartbeatIntent() {
        Intent i = new Intent(this, LocationTrackingService.class).setAction(ACTION_HEARTBEAT);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? PendingIntent.getForegroundService(this, 1, i, flags)
                : PendingIntent.getService(this, 1, i, flags);
    }

    private void cancelHeartbeat() {
        AlarmManager am = (AlarmManager) getSystemService(Context.ALARM_SERVICE);
        if (am != null) am.cancel(heartbeatIntent());
    }

    private void onHeartbeat() {
        // Keep the CPU up for the fix and the upload, then let it sleep.
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        PowerManager.WakeLock beat = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:heartbeat");
        beat.setReferenceCounted(false);
        beat.acquire(HEARTBEAT_WAKE_MS);
        // The alarm may have restarted the process: listen again if so.
        readSteps();
        if (!updatesRequested) requestUpdates();
        // A drive whose last fix came minutes ago (parked indoors, no fix at
        // all) ends here, and the permission may have been granted or taken back.
        MotionModeSelector.Mode before = motion.mode();
        if (motion.tick(System.currentTimeMillis())) onModeChanged(before);
        registerTransitions();
        registerStatusReceiver();
        if (System.currentTimeMillis() - lastUploadAt >= HEARTBEAT_MS) requestFreshFix();
        scheduleHeartbeat();
    }

    /** One fresh fix (fused, or from each enabled provider), for the heartbeat. */
    @SuppressWarnings("deprecation")
    private void requestFreshFix() {
        if (fused == null && locationManager == null) return;
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) return;
        try {
            if (fused != null) {
                fused.getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY, null)
                        .addOnSuccessListener(l -> {
                            if (l != null) consider(l);
                        });
            } else {
                for (String provider : new String[]{
                        LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
                    if (locationManager.isProviderEnabled(provider)) {
                        locationManager.requestSingleUpdate(provider, listener, Looper.getMainLooper());
                    }
                }
            }
        } catch (SecurityException e) {
            Log.w(TAG, "fresh fix denied", e);
        }
        // Nothing may arrive (indoors, no GPS): re-send what we hold, which
        // carries its own fix time, so the server can tell it is not new.
        handler.postDelayed(() -> {
            Location held = lastKnown;
            if (System.currentTimeMillis() - lastUploadAt >= HEARTBEAT_MS && held != null
                    && held.getTime() > lastUploadedFixTime) {
                upload(held, true);
            }
        }, 60_000L);
    }

    /**
     * @param force upload even inside the throttle window, used for a seeded
     *              fix and the heartbeat
     */
    private void upload(Location location, boolean force) {
        long now = System.currentTimeMillis();
        if (username == null || username.isEmpty()) {
            Log.w(TAG, "no username persisted; not uploading");
            return;
        }
        if (!force && now - lastUploadAt < motion.mode().uploadEveryMs) {
            return;
        }
        if (uploading) {
            return;
        }
        uploading = true;
        final String activity = motion.mode().activity;
        Thread worker = new Thread(() -> {
            try {
                doUpload(location, activity);
                lastUploadAt = System.currentTimeMillis();
                lastUploadedFixTime = location.getTime();
            } finally {
                uploading = false;
            }
        }, "location-upload");
        worker.start();
    }

    private void doUpload(Location location, String activity) {
        android.content.SharedPreferences p = TokenBridgePlugin.prefs(this);
        String token = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
        String serverUrl = p.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
        if (token == null || serverUrl == null || token.isEmpty() || serverUrl.isEmpty()) {
            // Not logged in (or logged out). Nothing to do; the service is
            // stopped by the JS side in that case.
            Log.w(TAG, "missing credentials; not uploading");
            return;
        }
        final String user = username;
        JSONObject body;
        try {
            body = fixBody(location, activity);
        } catch (Exception e) {
            Log.e(TAG, "could not build the upload", e);
            return;
        }
        // Fixes kept from a stretch without signal go first, oldest first: the
        // server takes the last fix it gets as the current position, and Geo
        // skips a fix older than its newest when it builds trips.
        LocationOutbox.Sent sent = LocationOutbox.sendLive(LocationOutbox.store(this), user, body,
                b -> LocationOutbox.send(serverUrl, token, user, b));
        switch (sent) {
            case OK:
                Log.i(TAG, "uploaded " + user);
                break;
            case RETRY:
                Log.i(TAG, "upload kept for later");
                break;
            default:
                // Refused (a 401/403/404/422): the same fix would be refused again.
                Log.w(TAG, "upload refused; fix dropped");
                break;
        }
    }

    private JSONObject fixBody(Location location, String activity) throws Exception {
        JSONObject body = fixFields(location.getLatitude(), location.getLongitude(),
                location.hasAccuracy() ? location.getAccuracy() : Double.NaN,
                location.hasSpeed() ? location.getSpeed() : Double.NaN,
                location.hasBearing() ? location.getBearing() : Double.NaN,
                location.getTime(), System.currentTimeMillis(), activity);
        // The family cards show it; without it each upload from here blanked
        // the battery the app had reported.
        int battery = batteryPercent();
        if (battery >= 0) body.put("battery", battery);
        // Lets the low battery notice re-arm as soon as the phone is plugged in.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            BatteryManager bm = (BatteryManager) getSystemService(Context.BATTERY_SERVICE);
            if (bm != null) body.put("charging", bm.isCharging());
        }
        putPhoneStatus(body);
        // Steps are filed under the fix's day on the server, so only with a
        // recent reading from the same local day as the fix (a held fix from
        // before midnight would file today's count under yesterday, and the
        // reverse would carry yesterday's total into today).
        long now = System.currentTimeMillis();
        String fixDay = java.time.Instant.ofEpochMilli(location.getTime())
                .atZone(java.time.ZoneId.systemDefault()).toLocalDate().toString();
        if (todaySteps >= 0 && now - todayStepsAt <= STEPS_FRESH_MS && fixDay.equals(todayStepsDay)) {
            body.put("daily_steps", todaySteps);
        }
        // Which device this is: someone signed in on a phone and a tablet
        // uploads from both, and only one may say where they are.
        body.put("device_id", PushGateway.deviceId(this));
        body.put("device_name", deviceName());
        body.put("user_id", userId != null ? userId : username);
        if (userId != null) {
            body.put("username", username);
        }
        return body;
    }

    /** "Google Pixel 10": the device's maker and model, for people to read. */
    static String deviceName() {
        String maker = Build.MANUFACTURER == null ? "" : Build.MANUFACTURER.trim();
        String model = Build.MODEL == null ? "" : Build.MODEL.trim();
        if (!maker.isEmpty() && !model.toLowerCase(java.util.Locale.ROOT).startsWith(maker.toLowerCase(java.util.Locale.ROOT))) {
            model = Character.toUpperCase(maker.charAt(0)) + maker.substring(1) + " " + model;
        }
        return model.isEmpty() ? "Android" : model;
    }

    /**
     * The fix itself, apart from battery, steps and identity. NaN means the
     * fix does not have that value.
     *
     * Speed is sent only when the fix has one. A network fix has none, and it
     * used to be sent as 0, so a phone doing 36 mph was shown as stationary;
     * the server works one out from the fixes when it is missing.
     */
    static JSONObject fixFields(double lat, double lon, double accuracyM, double speedMps, double bearing,
                                long fixTimeMs, long nowMs, String activity) throws Exception {
        JSONObject body = new JSONObject();
        body.put("latitude", lat);
        body.put("longitude", lon);
        body.put("accuracy", Double.isNaN(accuracyM) ? 0 : accuracyM);
        if (!Double.isNaN(speedMps)) body.put("speed", speedMps);
        // When the fix was taken, not when it is sent: a cached last-known fix
        // (hours old, possibly) used to be reported as current.
        body.put("timestamp", (fixTimeMs > 0 ? fixTimeMs : nowMs) / 1000.0);
        if (!Double.isNaN(bearing)) body.put("bearing", bearing);
        // driving, cycling, walking, still or unknown: what the phone was doing.
        if (activity != null) body.put("activity", activity);
        return body;
    }

    /**
     * Take one pedometer reading into the step ledger, so steps keep syncing
     * (they ride along with the location uploads) while the app is closed.
     * Before this only the open app read the counter: a phone whose app was
     * not opened reported almost no steps, and a day with no reading in its
     * last hour could not be credited across midnight at all.
     */
    private void readSteps() {
        if (!getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_STEPS, false)) {
            todaySteps = -1;
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
                && checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        final SensorManager sm = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        final Sensor counter = sm == null ? null : sm.getDefaultSensor(Sensor.TYPE_STEP_COUNTER);
        if (counter == null) return;
        if (stepLedger == null) stepLedger = new StepLedger(this);
        final SensorEventListener once = new SensorEventListener() {
            @Override
            public void onSensorChanged(SensorEvent event) {
                sm.unregisterListener(this);
                try {
                    todaySteps = StepCounterPlugin.recordReading(
                            LocationTrackingService.this, stepLedger, event.values[0]);
                    todayStepsAt = System.currentTimeMillis();
                    todayStepsDay = java.time.LocalDate.now().toString();
                } catch (RuntimeException e) {
                    Log.w(TAG, "step reading failed", e);
                }
            }

            @Override
            public void onAccuracyChanged(Sensor sensor, int accuracy) {
                // Not used.
            }
        };
        // The counter delivers its current value as soon as it is registered.
        if (sm.registerListener(once, counter, SensorManager.SENSOR_DELAY_NORMAL)) {
            handler.postDelayed(() -> sm.unregisterListener(once), 10_000L);
        }
    }

    /**
     * The charging state and the next alarm, as the companion app reported
     * them (the hub's battery_state, charger_type and next_alarm sensors).
     * next_alarm_ms is 0 when no alarm is set, so the server can tell "none"
     * from an older app that does not send it.
     */
    private void putPhoneStatus(JSONObject body) throws Exception {
        Intent battery = null;
        try {
            battery = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        } catch (RuntimeException e) {
            Log.w(TAG, "battery status unavailable", e);
        }
        // Not sent when Android will not say: the hub then shows the sensor
        // unavailable, never a guess. Logged, so a phone that never says is visible.
        if (battery == null) {
            Log.w(TAG, "no battery status from Android; battery_state and charger_type not sent");
        } else {
            int status = battery.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
            int plugged = battery.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1);
            String state = PhoneStatusRules.batteryState(status);
            if ("unknown".equals(state)) {
                Log.w(TAG, "battery status " + status + " is not one the companion app names; battery_state not sent");
            } else {
                body.put("battery_state", state);
            }
            String charger = PhoneStatusRules.chargerType(plugged);
            if ("unknown".equals(charger)) {
                Log.w(TAG, "no plug reading (" + plugged + "); charger_type not sent");
            } else {
                body.put("charger_type", charger);
            }
        }
        AlarmManager am = (AlarmManager) getSystemService(Context.ALARM_SERVICE);
        if (am == null) {
            Log.w(TAG, "no AlarmManager; next_alarm_ms not sent");
            return;
        }
        AlarmManager.AlarmClockInfo next = null;
        try {
            next = am.getNextAlarmClock();
        } catch (RuntimeException e) {
            Log.w(TAG, "next alarm unreadable; next_alarm_ms not sent", e);
            return;
        }
        long at = next == null ? 0L : PhoneStatusRules.nextAlarmMs(next.getTriggerTime(), System.currentTimeMillis());
        body.put("next_alarm_ms", at);
        if (at > 0 && next.getShowIntent() != null) {
            String pkg = PhoneStatusRules.alarmPackage(next.getShowIntent().getCreatorPackage());
            if (pkg != null) body.put("next_alarm_package", pkg);
        }
    }

    /** Listen for the charger and the next alarm while tracking (registered once). */
    private void registerStatusReceiver() {
        if (statusReceiverRegistered) return;
        IntentFilter filter = new IntentFilter();
        filter.addAction(Intent.ACTION_POWER_CONNECTED);
        filter.addAction(Intent.ACTION_POWER_DISCONNECTED);
        filter.addAction(AlarmManager.ACTION_NEXT_ALARM_CLOCK_CHANGED);
        try {
            // System broadcasts still reach a receiver that is not exported.
            ContextCompat.registerReceiver(this, statusReceiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED);
            statusReceiverRegistered = true;
        } catch (RuntimeException e) {
            Log.w(TAG, "status receiver refused", e);
        }
    }

    private void unregisterStatusReceiver() {
        if (!statusReceiverRegistered) return;
        statusReceiverRegistered = false;
        try {
            unregisterReceiver(statusReceiver);
        } catch (IllegalArgumentException ignored) {
            // Already gone.
        }
    }

    /** A status change: report now, or at the end of the rate limit's gap. */
    private void requestStatusReport() {
        long wait = statusLimiter.request(System.currentTimeMillis());
        if (wait == PhoneStatusRules.Limiter.ALREADY_PENDING) return;
        handler.postDelayed(this::sendStatusReport, wait);
    }

    /**
     * Re-send the fix held, now carrying the new status. It keeps its own fix
     * time, so the server does not take it for a new position. With no fix
     * held yet there is nothing to attach the status to; the next fix carries it.
     */
    private void sendStatusReport() {
        Location held = lastKnown;
        if (held != null && uploading) {
            // An upload in flight may have read the status before the change.
            handler.postDelayed(this::sendStatusReport, 5_000L);
            return;
        }
        statusLimiter.sent(System.currentTimeMillis());
        if (held != null) upload(held, true);
    }

    /** Battery charge 0-100, or -1 when the device will not say. */
    private int batteryPercent() {
        BatteryManager bm = (BatteryManager) getSystemService(Context.BATTERY_SERVICE);
        if (bm == null) return -1;
        int pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        return pct >= 0 && pct <= 100 ? pct : -1;
    }

    private void stopTracking() {
        handler.removeCallbacksAndMessages(null);
        cancelHeartbeat();
        updatesRequested = false;
        removeUpdates();
        removeTransitions();
        unregisterStatusReceiver();
        postDrivingEvents(drivingMonitor.finish());
        stopCrashListening();
        releaseWakeLock();
        stopForeground(true);
        stopSelf();
    }

    @Override
    public void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        releaseWakeLock();
        removeUpdates();
        stopCrashListening();
        unregisterStatusReceiver();
        if (instance == this) instance = null;
        super.onDestroy();
    }

    private void removeUpdates() {
        if (fused != null) fused.removeLocationUpdates(fusedCallback);
        requestedMode = null;
        if (locationManager != null) {
            try {
                locationManager.removeUpdates(listener);
            } catch (SecurityException ignored) {
                // Nothing to remove.
            }
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
