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
 * already turned on in the app, so they should not sit in the notification
 * bar. Their notices go to a channel that is created turned off. On Android
 * 13 and later the services keep running and the system lists them only under
 * "Active apps" at the bottom of the shade, where they can also be stopped.
 * Chat messages and family notices use their own channels and still pop up
 * as ordinary notifications that clear once read.
 *
 * Android 12 and earlier may show a system "running in the background" notice
 * instead. Someone who wants the notice back can turn the channel on in the
 * app's notification settings.
 */
final class BackgroundNotice {
    static final String CHANNEL_ID = "background_hidden";
    private static final String GROUP = "com.jarvisos.app.BACKGROUND";

    /** Channels used by earlier builds, which showed the notice. A channel's
     *  importance cannot be changed by the app once created, so they are removed. */
    private static final String[] RETIRED_CHANNELS = {
        "location_tracking", "chat_connection", "background_services",
    };

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
                CHANNEL_ID, "Background services", NotificationManager.IMPORTANCE_NONE);
        ch.setDescription("Location sharing and family chat running in the background. "
                + "Off by default; they run either way and show under Active apps.");
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
                // Android asks for at least LOW on a service notice; the
                // channel, not the priority, is what keeps it out of sight.
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setGroup(GROUP);
        if (onTap != null) b.setContentIntent(onTap);
        return b.build();
    }
}
