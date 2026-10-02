package com.jarvisos.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
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
 */
public class LocationTrackingService extends Service {
    private static final String TAG = "LocationTracking";
    private static final String CHANNEL_ID = "location_tracking";
    private static final int NOTIFICATION_ID = 0x10A1;
    private static final long MIN_TIME_MS = 30_000L;
    private static final float MIN_DISTANCE_M = 5f;
    /**
     * Re-send even when nothing moved. Without this a stationary device produces
     * no LocationManager callback at all, so the family's view of "still there"
     * silently ages out.
     */
    private static final long HEARTBEAT_MS = 5 * 60 * 1000L;
    private static final long UPLOAD_TIMEOUT_MS = 15_000L;

    private LocationManager locationManager;
    private PowerManager.WakeLock wakeLock;
    private volatile Location lastKnown;
    private volatile long lastUploadAt = 0L;
    private volatile String username;
    private volatile String userId;
    private volatile boolean uploading = false;

    private final LocationListener listener = new LocationListener() {
        @Override
        public void onLocationChanged(Location location) {
            lastKnown = location;
            upload(location, false);
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

    @Override
    public void onCreate() {
        super.onCreate();
        locationManager = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        createChannel();
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
            if (intent.getStringExtra("username") != null) {
                persistIdentity(intent.getStringExtra("username"), intent.getStringExtra("userId"));
            }
        }
        startForegroundCompat();
        acquireWakeLock();
        requestUpdates();
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

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return;
        NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID, "Location sharing", NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Keeps sharing your location with your family while the app is closed.");
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);
    }

    private void startForegroundCompat() {
        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("Sharing your location")
                .setContentText("Jarvis keeps your family map up to date while the app is closed.")
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .build();
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
        if (locationManager == null) return;
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "fine location not granted; cannot track");
            return;
        }
        try {
            locationManager.removeUpdates(listener);
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.GPS_PROVIDER, MIN_TIME_MS, MIN_DISTANCE_M, listener);
            }
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.NETWORK_PROVIDER, MIN_TIME_MS, MIN_DISTANCE_M, listener);
            }
            // Seed from the freshest cached fix so the family map is not blank
            // for the first interval after the service starts.
            Location cached = bestLastKnown();
            if (cached != null) {
                lastKnown = cached;
                upload(cached, true);
            }
            Log.i(TAG, "tracking started for " + username);
        } catch (SecurityException e) {
            Log.e(TAG, "requestLocationUpdates denied", e);
        }
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
     * @param force upload even inside the heartbeat window, used for a seeded fix
     */
    private void upload(Location location, boolean force) {
        long now = System.currentTimeMillis();
        if (username == null || username.isEmpty()) {
            Log.w(TAG, "no username persisted; not uploading");
            return;
        }
        if (!force && now - lastUploadAt < HEARTBEAT_MS) {
            return;
        }
        if (uploading) {
            return;
        }
        uploading = true;
        Thread worker = new Thread(() -> {
            try {
                doUpload(location);
                lastUploadAt = System.currentTimeMillis();
            } finally {
                uploading = false;
            }
        }, "location-upload");
        worker.start();
    }

    private void doUpload(Location location) {
        android.content.SharedPreferences p = TokenBridgePlugin.prefs(this);
        String token = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
        String serverUrl = p.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
        if (token == null || serverUrl == null || token.isEmpty() || serverUrl.isEmpty()) {
            // Not logged in (or logged out). Nothing to do; the service is
            // stopped by the JS side in that case.
            Log.w(TAG, "missing credentials; not uploading");
            return;
        }
        String base = serverUrl.endsWith("/") ? serverUrl.substring(0, serverUrl.length() - 1) : serverUrl;
        String primary = base + "/api/users/" + android.net.Uri.encode(username) + "/location";
        String fallback = base + "/api/users/location";
        try {
            int status = post(primary, token, location);
            if (status == 404) {
                // Older gateways expose only the collection route.
                status = post(fallback, token, location);
            }
            if (status >= 200 && status < 300) {
                Log.i(TAG, "uploaded " + username + " (" + status + ")");
            } else {
                Log.w(TAG, "upload failed HTTP " + status);
            }
        } catch (Exception e) {
            Log.e(TAG, "upload threw", e);
        }
    }

    private int post(String url, String token, Location location) throws Exception {
        JSONObject body = new JSONObject();
        body.put("latitude", location.getLatitude());
        body.put("longitude", location.getLongitude());
        body.put("accuracy", location.hasAccuracy() ? location.getAccuracy() : 0);
        body.put("speed", location.hasSpeed() ? location.getSpeed() : 0);
        body.put("timestamp", System.currentTimeMillis() / 1000.0);
        body.put("user_id", userId != null ? userId : username);
        if (userId != null) {
            body.put("username", username);
        }

        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(10_000);
        conn.setReadTimeout((int) UPLOAD_TIMEOUT_MS);
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

    private void stopTracking() {
        if (locationManager != null) {
            try {
                locationManager.removeUpdates(listener);
            } catch (SecurityException ignored) {
                // Nothing to remove.
            }
        }
        releaseWakeLock();
        stopForeground(true);
        stopSelf();
    }

    @Override
    public void onDestroy() {
        releaseWakeLock();
        if (locationManager != null) {
            try {
                locationManager.removeUpdates(listener);
            } catch (SecurityException ignored) {
                // Nothing to remove.
            }
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
