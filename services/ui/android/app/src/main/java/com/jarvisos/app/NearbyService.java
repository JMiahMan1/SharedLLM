package com.jarvisos.app;

import android.app.Notification;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

/**
 * Keeps the Nearby & offline engine (Bluetooth LE: Find family nearby, the
 * offline chat, offline mode, a child's parent link) running while location
 * sharing is off. When Wander's location service runs, it hosts the engine
 * instead and this service stops, so there is never a second notification
 * ({@link NearbyParts#ensure}). One quiet notification, in the background
 * channel; stopped when no part is on.
 */
public class NearbyService extends Service {
    private static final String TAG = "NearbyService";
    private static final int NOTIFICATION_ID = 0x10A7;
    private static volatile boolean running;

    static boolean running() {
        return running;
    }

    static void start(Context context) {
        if (running) return;
        context.startForegroundService(new Intent(context, NearbyService.class));
    }

    static void stop(Context context) {
        if (!running) return;
        context.stopService(new Intent(context, NearbyService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        running = true;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (!startForegroundCompat()) return START_NOT_STICKY;
        if (NearbyParts.host(this) != NearbyRules.Host.OWN) {
            // Wander's service took over, or no part needs the engine any more.
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        Proximity.start(this);
        return START_STICKY;
    }

    /**
     * Android 14+ needs a type: connectedDevice, which Android allows once a
     * Nearby devices permission is granted (the engine only runs then).
     */
    private boolean startForegroundCompat() {
        Notification n = BackgroundNotice.build(this, PushNotifier.STATUS_ICON, "Nearby & offline is on", null);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
            } else {
                startForeground(NOTIFICATION_ID, n);
            }
            return true;
        } catch (SecurityException | IllegalStateException e) {
            Log.w(TAG, "Android would not let the nearby engine run now: " + e.getMessage());
            stopSelf();
            return false;
        }
    }

    @Override
    public void onDestroy() {
        running = false;
        // Handed over to Wander's service: leave the engine running there.
        if (!LocationTrackingService.running()) Proximity.stop();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
