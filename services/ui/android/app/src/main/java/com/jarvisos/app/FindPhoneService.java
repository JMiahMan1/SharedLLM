package com.jarvisos.app;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;
import android.util.Log;

import androidx.core.app.NotificationCompat;

/**
 * Rings the phone when Jarvis receives a {@code find_phone} notice.
 *
 * A lost phone may be silent, so this uses the alarm stream rather than the
 * notification stream, turns the alarm volume up for the ring, and loops the
 * default alarm tone with vibration for a minute. The ongoing notification
 * carries Stop; dismissing it or signing out stops the ring too. Do Not
 * Disturb can still mute the alarm unless alarms are allowed in DND; Android
 * gives an app no way around that without notification-policy access.
 */
public class FindPhoneService extends Service {
    private static final String TAG = "FindPhone";
    private static final String ACTION_STOP = "stop";
    private static final int NOTIFICATION_ID = 0x10E1;
    private static final long RING_MS = 60_000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable stopRunnable = new Runnable() {
        @Override
        public void run() {
            stopRinging();
            stopForeground(true);
            stopSelf();
        }
    };

    private MediaPlayer player;
    private Vibrator vibrator;
    private PowerManager.WakeLock wakeLock;
    private AudioManager audio;
    private int previousAlarmVolume = -1;

    static void start(Context context) {
        Intent intent = new Intent(context, FindPhoneService.class);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
        } catch (Exception e) {
            // Background-start restrictions: the phone can still show the notice.
            Log.w(TAG, "could not start: " + e.getMessage());
        }
    }

    static void stop(Context context) {
        try {
            context.stopService(new Intent(context, FindPhoneService.class));
        } catch (Exception e) {
            Log.w(TAG, "could not stop: " + e.getMessage());
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        PushNotifier.ensureChannels(this);
        audio = getSystemService(AudioManager.class);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopRinging();
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        startForegroundCompat();
        startRinging();
        handler.removeCallbacks(stopRunnable);
        handler.postDelayed(stopRunnable, RING_MS);
        // If the process dies there is nothing useful to resume.
        return START_NOT_STICKY;
    }

    private void startRinging() {
        stopRinging();

        PowerManager pm = getSystemService(PowerManager.class);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:find-phone");
            wakeLock.acquire(RING_MS + 10_000L);
        }

        if (audio != null) {
            try {
                previousAlarmVolume = audio.getStreamVolume(AudioManager.STREAM_ALARM);
                audio.setStreamVolume(AudioManager.STREAM_ALARM,
                        audio.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0);
            } catch (Exception e) {
                previousAlarmVolume = -1;
                Log.w(TAG, "could not raise the alarm volume: " + e.getMessage());
            }
        }

        Uri uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
        if (uri == null) uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE);
        if (uri == null) uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
        if (uri != null) {
            try {
                MediaPlayer p = new MediaPlayer();
                p.setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build());
                p.setDataSource(this, uri);
                p.setLooping(true);
                p.prepare();
                p.start();
                player = p;
            } catch (Exception e) {
                Log.w(TAG, "no alarm sound: " + e.getMessage());
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            VibratorManager vm = getSystemService(VibratorManager.class);
            if (vm != null) vibrator = vm.getDefaultVibrator();
        } else {
            vibrator = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
        }
        if (vibrator != null && vibrator.hasVibrator()) {
            long[] pattern = {0, 700, 500, 700, 500, 1000};
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator.vibrate(VibrationEffect.createWaveform(pattern, 0));
            } else {
                vibrator.vibrate(pattern, 0);
            }
        }
    }

    private void stopRinging() {
        handler.removeCallbacks(stopRunnable);
        if (player != null) {
            try {
                if (player.isPlaying()) player.stop();
            } catch (Exception ignored) {
                // Already stopped.
            }
            player.release();
            player = null;
        }
        if (vibrator != null) {
            vibrator.cancel();
            vibrator = null;
        }
        if (wakeLock != null) {
            if (wakeLock.isHeld()) wakeLock.release();
            wakeLock = null;
        }
        if (audio != null && previousAlarmVolume >= 0) {
            try {
                audio.setStreamVolume(AudioManager.STREAM_ALARM, previousAlarmVolume, 0);
            } catch (Exception ignored) {
                // Volume changed under us; nothing to restore.
            }
        }
        previousAlarmVolume = -1;
    }

    private void startForegroundCompat() {
        Notification notification = buildNotification();
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
        } catch (Exception e) {
            Log.w(TAG, "foreground refused: " + e.getMessage());
        }
    }

    private Notification buildNotification() {
        PendingIntent stop = stopIntent();
        return new NotificationCompat.Builder(
                this, PushCategories.channelId("family_safety", 5))
                .setSmallIcon(PushCategories.icon("family_safety", 5))
                .setContentTitle("Find my phone")
                .setContentText("Your phone is ringing.")
                .setStyle(new NotificationCompat.BigTextStyle()
                        .bigText("Your phone is ringing. Tap Stop to silence it."))
                .setPriority(NotificationCompat.PRIORITY_MAX)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .setAutoCancel(false)
                .setOnlyAlertOnce(true)
                .setSilent(true)
                .setShowWhen(false)
                .setContentIntent(PushNotifier.openIntent(this, "/", NOTIFICATION_ID))
                .setDeleteIntent(stop)
                .addAction(new NotificationCompat.Action.Builder(0, "Stop", stop).build())
                .build();
    }

    private PendingIntent stopIntent() {
        int flags = PendingIntent.FLAG_UPDATE_CURRENT
                | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0);
        return PendingIntent.getService(this, NOTIFICATION_ID,
                new Intent(this, FindPhoneService.class).setAction(ACTION_STOP), flags);
    }

    @Override
    public void onDestroy() {
        stopRinging();
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
