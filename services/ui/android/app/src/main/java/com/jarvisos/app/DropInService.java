package com.jarvisos.app;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import androidx.core.app.Person;

/**
 * The ongoing notification and foreground service of a family drop-in
 * ({@link DropInCall}). Its phoneCall + microphone type is what keeps the
 * WebView's microphone and speaker working while the screen is off or another
 * app is open. Hang up leaves the call.
 */
public class DropInService extends Service {
    private static final String TAG = "DropInService";
    private static final int NOTIFICATION_ID = 0x0D1C;
    private static final String ACTION_HANGUP = "hangup";

    static void start(Context context, String room, String name) {
        Intent intent = new Intent(context, DropInService.class)
                .putExtra(DropInCall.EXTRA_ROOM, room)
                .putExtra(DropInCall.EXTRA_NAME, name);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
        } catch (Exception e) {
            Log.w(TAG, "could not start: " + e.getMessage());
        }
    }

    static void stop(Context context) {
        try {
            context.stopService(new Intent(context, DropInService.class));
        } catch (Exception ignored) {
            // Not running.
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_HANGUP.equals(intent.getAction())) {
            DropInCall.hangUp(this);
            stopSelf();
            return START_NOT_STICKY;
        }
        String room = intent == null ? null : intent.getStringExtra(DropInCall.EXTRA_ROOM);
        String name = intent == null ? null : intent.getStringExtra(DropInCall.EXTRA_NAME);
        if (room == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        Notification notification = build(room, name == null ? "Family call" : name);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                startForeground(NOTIFICATION_ID, notification,
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL
                                | ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
        } catch (Exception e) {
            // Refused (no active Telecom call to vouch for us): the call can
            // still go ahead while the app is on screen.
            Log.w(TAG, "foreground refused: " + e.getMessage());
        }
        return START_NOT_STICKY;
    }

    private Notification build(String room, String name) {
        PendingIntent open = PendingIntent.getActivity(this, NOTIFICATION_ID,
                DropInCall.openIntent(this, room),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent hangup = PendingIntent.getService(this, NOTIFICATION_ID,
                new Intent(this, DropInService.class).setAction(ACTION_HANGUP),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Person caller = new Person.Builder().setName(name).setImportant(true).build();
        return new NotificationCompat.Builder(this, PushCategories.channelId("call", 4))
                .setSmallIcon(PushCategories.icon("call", 4))
                .setContentTitle(name)
                .setContentText("Drop In: you're live")
                .setStyle(NotificationCompat.CallStyle.forOngoingCall(caller, hangup))
                .setCategory(NotificationCompat.CATEGORY_CALL)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(open)
                // Opens the call page over the lock screen when the app
                // could not be brought up directly.
                .setFullScreenIntent(open, true)
                .build();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
