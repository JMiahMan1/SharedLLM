package com.jarvisos.app;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.util.Log;

import androidx.core.app.NotificationCompat;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Shows Beloved's special-day reminders and keeps them across a reboot.
 *
 * The list the page last synced is kept in this app's private preferences
 * (only the ids, times and the text the page chose, which is generic unless
 * the owner allowed details) and armed with AlarmManager. A reboot or an app
 * update clears alarms, so BOOT_COMPLETED and MY_PACKAGE_REPLACED arm them
 * again. The "Special days" channel is made the first time a reminder is
 * shown, so it never appears on a phone whose owner does not use Beloved, and
 * the lock screen only ever shows the generic line.
 */
public class BelovedReminderReceiver extends BroadcastReceiver {
    private static final String TAG = "BelovedReminders";
    static final String ACTION_FIRE = "com.jarvisos.app.beloved.REMINDER";
    static final String CHANNEL = "jarvis_special_days";
    static final int MAX = 64;
    private static final String PREFS = "jarvis_beloved_reminders";
    private static final String KEY = "list";
    private static final String GENERIC = "Something special is coming up. Open Jarvis to see.";

    /** Cancel what was set, keep and arm the new list; how many were armed. */
    static int replace(Context context, JSONArray reminders) {
        Context app = context.getApplicationContext();
        for (JSONObject r : list(load(app))) cancel(app, r.optInt("id"));
        prefs(app).edit().putString(KEY, reminders.toString()).apply();
        return armAll(app);
    }

    private static int armAll(Context app) {
        int armed = 0;
        long now = System.currentTimeMillis();
        for (JSONObject r : list(load(app))) {
            if (r.optLong("at") > now) {
                arm(app, r);
                armed++;
            }
        }
        return armed;
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        Context app = context.getApplicationContext();
        String action = intent.getAction();
        if (ACTION_FIRE.equals(action)) {
            show(app, intent.getIntExtra("id", 0), intent.getStringExtra("title"), intent.getStringExtra("body"));
            return;
        }
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            armAll(app);
        }
    }

    private static void show(Context app, int id, String title, String body) {
        NotificationManager nm = app.getSystemService(NotificationManager.class);
        if (nm == null) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm.getNotificationChannel(CHANNEL) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Special days", NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription("Reminders for the special days you keep in your private chat.");
            ch.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
            nm.createNotificationChannel(ch);
        }
        String text = body == null || body.isEmpty() ? GENERIC : body;
        Notification publicVersion = new NotificationCompat.Builder(app, CHANNEL)
                .setSmallIcon(R.drawable.mi_favorite)
                .setContentTitle("Jarvis")
                .setContentText(GENERIC)
                .build();
        NotificationCompat.Builder b = new NotificationCompat.Builder(app, CHANNEL)
                .setSmallIcon(R.drawable.mi_favorite)
                .setContentTitle(title == null || title.isEmpty() ? "Jarvis" : title)
                .setContentText(text)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(text))
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setPublicVersion(publicVersion)
                .setAutoCancel(true)
                .setContentIntent(PushNotifier.openIntent(app, "/family", 0x5EC1A1 ^ id));
        try {
            nm.notify(0x5EC1A1 ^ id, b.build());
        } catch (SecurityException e) {
            Log.w(TAG, "notifications are not allowed", e);
        }
    }

    private static void arm(Context app, JSONObject r) {
        AlarmManager am = (AlarmManager) app.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        PendingIntent pi = pending(app, r.optInt("id"), r.optString("title"), r.optString("body"));
        long at = r.optLong("at");
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
            else am.set(AlarmManager.RTC_WAKEUP, at, pi);
        } catch (SecurityException e) {
            am.set(AlarmManager.RTC, at, pi);
        }
    }

    private static void cancel(Context app, int id) {
        AlarmManager am = (AlarmManager) app.getSystemService(Context.ALARM_SERVICE);
        if (am != null) am.cancel(pending(app, id, "", ""));
    }

    private static PendingIntent pending(Context app, int id, String title, String body) {
        Intent i = new Intent(app, BelovedReminderReceiver.class)
                .setAction(ACTION_FIRE)
                .putExtra("id", id)
                .putExtra("title", title)
                .putExtra("body", body);
        return PendingIntent.getBroadcast(app, id, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static SharedPreferences prefs(Context app) {
        return app.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static JSONArray load(Context app) {
        try {
            return new JSONArray(prefs(app).getString(KEY, "[]"));
        } catch (JSONException e) {
            return new JSONArray();
        }
    }

    private static java.util.List<JSONObject> list(JSONArray array) {
        java.util.List<JSONObject> out = new java.util.ArrayList<>();
        for (int i = 0; i < array.length(); i++) {
            JSONObject r = array.optJSONObject(i);
            if (r != null) out.add(r);
        }
        return out;
    }
}
