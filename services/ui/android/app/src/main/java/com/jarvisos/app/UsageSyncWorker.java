package com.jarvisos.app;

import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.provider.Settings;
import android.util.Log;
import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import com.getcapacitor.JSObject;
import com.jarvisos.app.widgets.WidgetApi;
import java.util.Calendar;
import java.util.Locale;
import java.util.concurrent.TimeUnit;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Screen time while the app is closed. The WebView posts today's usage only
 * while Jarvis is open, so a phone nobody opened Jarvis on reported nothing,
 * and a day's total stopped wherever the app was last open. This posts today
 * every half hour, and yesterday's finished day once, to the same
 * {@code /api/usage} the WebView uses (the server keeps the longest reading
 * per device, so the overlap is harmless).
 *
 * It also sends last night's screen-on stretches for the sleep estimate.
 *
 * Needs usage access and a signed-in key. Without usage access on a child's
 * phone (the server says the phone is protected), it reminds the person
 * holding it once a day to turn it on, since every screen-time number and
 * app limit the parents see comes from it. WorkManager keeps the schedule
 * across reboots and app updates.
 */
public class UsageSyncWorker extends Worker {
    private static final String TAG = "UsageSync";
    static final String WORK_NAME = "usage_sync";
    private static final String PREFS = "jarvis_usage_sync";
    private static final String KEY_YESTERDAY_SENT = "yesterday_sent";
    private static final String KEY_REMINDED_AT = "access_reminded_at";
    static final long REMINDER_GAP_MS = 24L * 60 * 60 * 1000;
    private static final int REMINDER_ID = 0x75736167; // "usag"

    public UsageSyncWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    static void schedule(Context context) {
        Constraints constraints = new Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build();
        PeriodicWorkRequest request =
            new PeriodicWorkRequest.Builder(UsageSyncWorker.class, 30, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build();
        WorkManager.getInstance(context.getApplicationContext())
            .enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request);
    }

    static void cancel(Context context) {
        WorkManager.getInstance(context.getApplicationContext()).cancelUniqueWork(WORK_NAME);
    }

    @NonNull
    @Override
    public Result doWork() {
        Context app = getApplicationContext();
        WidgetApi.ensureCredentials(app);
        String key = WidgetApi.apiKey(app);
        if (key == null || key.isEmpty()) return Result.success();
        if (!PhoneUsagePlugin.hasUsageAccess(app)) {
            remindIfProtected(app);
            return Result.success();
        }
        String deviceKey = TokenBridgePlugin.prefs(app).getString(TokenBridgePlugin.KEY_DEVICE_KEY, "");

        long now = System.currentTimeMillis();
        long today = PhoneUsagePlugin.startOfDay(now);
        Calendar previous = Calendar.getInstance();
        previous.setTimeInMillis(today);
        previous.add(Calendar.DAY_OF_YEAR, -1);
        long yesterday = previous.getTimeInMillis();
        SharedPreferences prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String yesterdayDay = localDay(yesterday);
        try {
            if (!yesterdayDay.equals(prefs.getString(KEY_YESTERDAY_SENT, ""))) {
                post(app, yesterdayDay, deviceKey, PhoneUsagePlugin.readDay(app, yesterday, today));
                prefs.edit().putString(KEY_YESTERDAY_SENT, yesterdayDay).apply();
            }
            post(app, localDay(now), deviceKey, PhoneUsagePlugin.readDay(app, today, now));
        } catch (Exception e) {
            Log.w(TAG, "usage sync failed: " + e.getMessage());
            return Result.retry();
        }
        try {
            postNight(app, deviceKey, today, now);
        } catch (Exception e) {
            // A device that has not registered yet (404) or an older server:
            // the day's usage above is unaffected, and the next run tries again.
            Log.w(TAG, "night sync failed: " + e.getMessage());
        }
        return Result.success();
    }

    /**
     * Last night's screen-on stretches, 18:00 the evening before to noon (or
     * now, before noon), the same window and endpoint the WebView uses.
     */
    private static void postNight(Context app, String deviceKey, long today, long now) throws Exception {
        if (deviceKey == null || deviceKey.isEmpty()) return;
        Calendar noon = Calendar.getInstance();
        noon.setTimeInMillis(today);
        noon.set(Calendar.HOUR_OF_DAY, 12);
        Calendar evening = Calendar.getInstance();
        evening.setTimeInMillis(today);
        evening.add(Calendar.DAY_OF_YEAR, -1);
        evening.set(Calendar.HOUR_OF_DAY, 18);
        long until = Math.min(noon.getTimeInMillis(), now);
        JSONArray intervals = PhoneUsagePlugin.readNight(app, evening.getTimeInMillis(), until);
        if (intervals.length() == 0) return;
        JSONObject body = new JSONObject();
        body.put("night", localDay(today));
        body.put("device_key", deviceKey);
        body.put("intervals", intervals);
        WidgetApi.post(app, "/api/usage/night", body);
    }

    /**
     * Once a day, on a child's phone without usage access, a notice that taps
     * through to the setting. Whether the phone is a child's is the server's
     * answer (the screen-time status's {@code protected}); any failure to ask
     * means no reminder this time.
     */
    private static void remindIfProtected(Context app) {
        SharedPreferences prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        long now = System.currentTimeMillis();
        if (now - prefs.getLong(KEY_REMINDED_AT, 0L) < REMINDER_GAP_MS) return;
        String user = TokenBridgePlugin.prefs(app).getString(TokenBridgePlugin.KEY_USERNAME, "");
        if (user == null || user.trim().isEmpty()) return;
        try {
            JSONObject status = WidgetApi.get(app, "/api/screen-time/" + Uri.encode(user.trim()) + "/status");
            if (!status.optBoolean("protected", false)) return;
        } catch (Exception e) {
            return;
        }
        PushNotifier.ensureChannels(app);
        NotificationManager nm = app.getSystemService(NotificationManager.class);
        if (nm == null) return;
        Intent settings = new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        PendingIntent tap = PendingIntent.getActivity(app, REMINDER_ID, settings,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        String body = "Jarvis needs usage access on this phone for screen time and app limits. "
            + "Tap, choose Jarvis, and allow it.";
        nm.notify(REMINDER_ID, new NotificationCompat.Builder(app, PushCategories.channelId("family_safety", 3))
            .setSmallIcon(PushCategories.icon("family_safety", 3))
            .setContentTitle("Turn on usage access")
            .setContentText(body)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(tap)
            .build());
        prefs.edit().putLong(KEY_REMINDED_AT, now).apply();
    }


    /** One day's reading in the shape the WebView sends; an empty day is not sent. */
    private static void post(Context app, String day, String deviceKey, JSObject reading) throws Exception {
        if (reading.optLong("millis", 0L) <= 0L) return;
        JSONArray apps = new JSONArray();
        JSONArray read = reading.optJSONArray("apps");
        for (int i = 0; read != null && i < read.length(); i++) {
            JSONObject a = read.getJSONObject(i);
            JSONObject row = new JSONObject();
            row.put("package", a.optString("package"));
            row.put("millis", a.optLong("millis", 0L));
            row.put("last_use", a.opt("lastUse"));
            apps.put(row);
        }
        JSONObject body = new JSONObject();
        body.put("day", day);
        body.put("device_key", deviceKey);
        body.put("millis", reading.optLong("millis", 0L));
        body.put("first_use", reading.opt("firstUse"));
        body.put("last_use", reading.opt("lastUse"));
        body.put("sessions", reading.optInt("sessions", 0));
        body.put("apps", apps);
        body.put("source", "phone");
        WidgetApi.post(app, "/api/usage", body);
    }

    /** The local calendar day, matching what the WebView sends. */
    static String localDay(long timestamp) {
        Calendar c = Calendar.getInstance();
        c.setTimeInMillis(timestamp);
        return String.format(Locale.ROOT, "%04d-%02d-%02d",
            c.get(Calendar.YEAR), c.get(Calendar.MONTH) + 1, c.get(Calendar.DAY_OF_MONTH));
    }
}
