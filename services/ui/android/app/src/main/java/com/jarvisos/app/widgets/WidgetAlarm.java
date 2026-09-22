package com.jarvisos.app.widgets;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import java.util.concurrent.TimeUnit;

/** Exact-ish 5 minute refresh. WorkManager PeriodicWork min interval is 15 min. */
public final class WidgetAlarm {
    public static final long INTERVAL_MS = TimeUnit.MINUTES.toMillis(5);
    private static final int REQ = 4242;

    private WidgetAlarm() {}

    public static void schedule(Context context) {
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        PendingIntent pi = pending(context);
        long trigger = System.currentTimeMillis() + INTERVAL_MS;
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, trigger, pi);
            } else {
                am.set(AlarmManager.RTC_WAKEUP, trigger, pi);
            }
        } catch (SecurityException ignored) {
            // Exact alarm permission missing — inexact is fine
            am.set(AlarmManager.RTC, trigger, pi);
        }
    }

    public static void cancel(Context context) {
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (am != null) am.cancel(pending(context));
    }

    private static PendingIntent pending(Context context) {
        Intent i = new Intent(context, WidgetRefreshReceiver.class);
        i.setAction(WidgetRefreshReceiver.ACTION);
        return PendingIntent.getBroadcast(context, REQ, i,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
}
