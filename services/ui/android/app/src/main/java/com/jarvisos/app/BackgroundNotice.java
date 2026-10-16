package com.jarvisos.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.os.Build;
import androidx.core.app.NotificationCompat;

/**
 * The notification Android requires while a service runs in the background.
 *
 * Location sharing and the family chat connection are things the person
 * already turned on in the app, so their notices should not take up the
 * status bar. They share one minimum-importance channel: no status bar icon,
 * no sound, folded into the "Silent" section of the shade, and grouped so the
 * two services show as one line. Android does not allow a foreground service
 * without a notification at all; this is the quietest form it accepts.
 */
final class BackgroundNotice {
    static final String CHANNEL_ID = "background_services";
    private static final String GROUP = "com.jarvisos.app.BACKGROUND";

    /** Channels used by earlier builds, at a louder importance. A channel's
     *  importance cannot be lowered once created, so they are removed. */
    private static final String[] RETIRED_CHANNELS = {"location_tracking", "chat_connection"};

    private BackgroundNotice() {}

    static void ensureChannel(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        for (String id : RETIRED_CHANNELS) {
            if (nm.getNotificationChannel(id) != null) nm.deleteNotificationChannel(id);
        }
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return;
        NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID, "Background services", NotificationManager.IMPORTANCE_MIN);
        ch.setDescription("Shown while location sharing or family chat runs in the background. "
                + "Hiding this does not stop either one.");
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);
    }

    static Notification build(Context context, int icon, String title, PendingIntent onTap) {
        ensureChannel(context);
        NotificationCompat.Builder b = new NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(icon)
                .setContentTitle(title)
                .setOngoing(true)
                .setSilent(true)
                .setShowWhen(false)
                .setPriority(NotificationCompat.PRIORITY_MIN)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setGroup(GROUP);
        if (onTap != null) b.setContentIntent(onTap);
        return b.build();
    }
}
