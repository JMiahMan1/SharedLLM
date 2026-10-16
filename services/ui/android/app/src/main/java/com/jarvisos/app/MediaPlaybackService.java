package com.jarvisos.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

/**
 * Keeps the app's own music playing once it is not on screen.
 *
 * The web player streams inside the WebView. Android freezes a backgrounded
 * app that has no foreground service within seconds, which is what stopped
 * playback whenever the app was switched away from or the screen went off. A
 * media-playback foreground service while something is playing prevents that,
 * and its MediaStyle notification (with a MediaSession) gives the lock screen,
 * headset buttons and Bluetooth play/pause/skip, all routed back to the web
 * player through {@link JarvisMediaPlugin}.
 */
public class MediaPlaybackService extends Service {
    private static final String TAG = "MediaPlayback";
    private static final String CHANNEL_ID = "media_playback";
    private static final int NOTIFICATION_ID = 0x10D1;

    interface ActionListener {
        void onAction(String action);
    }

    static volatile ActionListener listener;

    private MediaSession session;
    private String title = "Jarvis";
    private String artist = "";
    private boolean playing = false;

    static void update(Context context, String title, String artist, boolean playing) {
        Intent intent = new Intent(context, MediaPlaybackService.class)
                .setAction("update")
                .putExtra("title", title)
                .putExtra("artist", artist)
                .putExtra("playing", playing);
        try {
            if (playing && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
        } catch (Exception e) {
            Log.w(TAG, "could not update: " + e.getMessage());
        }
    }

    static void stop(Context context) {
        // stopService is always allowed. startService with a "stop" action is
        // refused from the background (Android 8+), which is where a paused
        // player is, so the paused notification used to stay behind.
        try {
            context.stopService(new Intent(context, MediaPlaybackService.class));
        } catch (Exception e) {
            Log.w(TAG, "could not stop: " + e.getMessage());
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
                NotificationChannel ch = new NotificationChannel(
                        CHANNEL_ID, "Now playing", NotificationManager.IMPORTANCE_LOW);
                ch.setDescription("Playback controls while music or an audiobook plays.");
                ch.setShowBadge(false);
                nm.createNotificationChannel(ch);
            }
        }
        session = new MediaSession(this, "JarvisMedia");
        session.setCallback(new MediaSession.Callback() {
            @Override public void onPlay() { dispatch("play"); }
            @Override public void onPause() { dispatch("pause"); }
            @Override public void onSkipToNext() { dispatch("next"); }
            @Override public void onSkipToPrevious() { dispatch("previous"); }
            @Override public void onStop() { dispatch("pause"); }
        });
        session.setActive(true);
    }

    private void dispatch(String action) {
        ActionListener l = listener;
        if (l != null) {
            l.onAction(action);
        } else {
            Log.w(TAG, "no web player listening for " + action);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if ("stop".equals(action)) {
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        if ("play".equals(action) || "pause".equals(action) || "next".equals(action) || "previous".equals(action)) {
            dispatch(action);
            return START_NOT_STICKY;
        }
        if (intent != null && "update".equals(action)) {
            title = valueOr(intent.getStringExtra("title"), "Jarvis");
            artist = valueOr(intent.getStringExtra("artist"), "");
            playing = intent.getBooleanExtra("playing", false);
        }
        publish();
        // Not sticky: after the process dies there is no web player to resume.
        return START_NOT_STICKY;
    }

    private static String valueOr(String s, String fallback) {
        return s == null || s.isEmpty() ? fallback : s;
    }

    private void publish() {
        session.setMetadata(new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, title)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, artist)
                .build());
        session.setPlaybackState(new PlaybackState.Builder()
                .setActions(PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_PLAY_PAUSE
                        | PlaybackState.ACTION_SKIP_TO_NEXT | PlaybackState.ACTION_SKIP_TO_PREVIOUS)
                .setState(playing ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED,
                        PlaybackState.PLAYBACK_POSITION_UNKNOWN, 1f)
                .build());

        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        b.setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(title)
                .setContentText(artist)
                .setOngoing(playing)
                .setShowWhen(false)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setContentIntent(openApp())
                .setDeleteIntent(command("stop", 4))
                .addAction(new Notification.Action.Builder(
                        android.R.drawable.ic_media_previous, "Previous", command("previous", 1)).build())
                .addAction(playing
                        ? new Notification.Action.Builder(android.R.drawable.ic_media_pause, "Pause", command("pause", 2)).build()
                        : new Notification.Action.Builder(android.R.drawable.ic_media_play, "Play", command("play", 2)).build())
                .addAction(new Notification.Action.Builder(
                        android.R.drawable.ic_media_next, "Next", command("next", 3)).build())
                .setStyle(new Notification.MediaStyle()
                        .setMediaSession(session.getSessionToken())
                        .setShowActionsInCompactView(0, 1, 2));
        Notification notification = b.build();
        try {
            if (playing) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
                } else {
                    startForeground(NOTIFICATION_ID, notification);
                }
            } else {
                // Paused: let Android reclaim us, but keep the controls up so
                // playback can be resumed from the shade or lock screen.
                stopForeground(false);
                NotificationManager nm = getSystemService(NotificationManager.class);
                if (nm != null) nm.notify(NOTIFICATION_ID, notification);
            }
        } catch (Exception e) {
            Log.w(TAG, "foreground refused: " + e.getMessage());
        }
    }

    private PendingIntent command(String action, int requestCode) {
        Intent intent = new Intent(this, MediaPlaybackService.class).setAction(action);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT
                | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0);
        return PendingIntent.getService(this, requestCode, intent, flags);
    }

    private PendingIntent openApp() {
        return PushNotifier.openIntent(this, "/media", 0x10D2);
    }

    @Override
    public void onDestroy() {
        if (session != null) {
            session.setActive(false);
            session.release();
        }
        stopForeground(true);
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm != null) nm.cancel(NOTIFICATION_ID);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
