package com.jarvisos.app;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import org.json.JSONObject;

/**
 * What happens after {@link CrashDetector} confirms a crash: a loud
 * notification counting down 60 s with "I'm OK" and "Get help", then, unless
 * cancelled, an automatic crash SOS to the family through
 * {@code POST /api/family/crash}.
 *
 * Everything runs off AlarmManager and SharedPreferences, not the tracking
 * service's handler: after a crash the phone may lie still with the screen
 * off, where a Handler delay stops counting and Android may reclaim the
 * process. An allow-while-idle alarm still fires in Doze, and the pending
 * crash read back from prefs survives the process going away.
 *
 * A send that fails for want of signal (likely, at a crash site) is retried
 * every 30 s for 15 minutes, after which the person is told to call someone
 * themselves.
 */
final class CrashAlert {
    private static final String TAG = "CrashAlert";
    static final String CHANNEL_ID = "crash_detection";
    private static final int NOTIFICATION_ID = 0x10A3;
    /** The outcome ("told", "could not reach"), separate so it outlives the countdown's. */
    private static final int RESULT_NOTIFICATION_ID = 0x10A4;
    static final long COUNTDOWN_MS = 60_000L;
    static final long RETRY_EVERY_MS = 30_000L;
    static final long RETRY_FOR_MS = 15 * 60 * 1000L;

    static final String ACTION_OK = "com.jarvisos.app.CRASH_OK";
    static final String ACTION_HELP = "com.jarvisos.app.CRASH_HELP";
    /** The countdown ran out. */
    static final String ACTION_FIRE = "com.jarvisos.app.CRASH_FIRE";
    static final String ACTION_RETRY = "com.jarvisos.app.CRASH_RETRY";

    private static final String PREFS = "jarvis_crash";
    private static final String KEY_CRASH = "crash";
    private static final String KEY_PHASE = "phase";
    private static final String KEY_SENDS_AT = "sends_at";
    private static final String KEY_AUTO = "auto";
    private static final String KEY_FIRST_TRY_AT = "first_try_at";
    private static final String PHASE_COUNTDOWN = "countdown";
    private static final String PHASE_SENDING = "sending";

    /** Prefs are read, checked and written under this, so a tap and the alarm cannot both send. */
    private static final Object LOCK = new Object();

    private CrashAlert() {}

    enum Outcome { SENT, RETRY, REFUSED }

    /**
     * A crash was confirmed: start the countdown. A crash already counting
     * down or being sent is left alone; the detector's own two-minute gap
     * makes that rare.
     */
    static void start(Context context, CrashDetector.Result result) {
        Context app = context.getApplicationContext();
        long sendsAt = System.currentTimeMillis() + COUNTDOWN_MS;
        synchronized (LOCK) {
            SharedPreferences p = prefs(app);
            if (p.getString(KEY_PHASE, null) != null) return;
            try {
                p.edit()
                        .putString(KEY_CRASH, result.toJson().toString())
                        .putString(KEY_PHASE, PHASE_COUNTDOWN)
                        .putLong(KEY_SENDS_AT, sendsAt)
                        .commit();
            } catch (Exception e) {
                Log.e(TAG, "could not keep the crash", e);
                return;
            }
        }
        Log.i(TAG, "crash confirmed: peak " + result.peakG + " g from " + result.speedBeforeMph + " mph");
        schedule(app, ACTION_FIRE, sendsAt);
        showCountdown(app, sendsAt);
    }

    /**
     * Put the countdown or the retries back after the process restarted or
     * the phone rebooted (which clears alarms). A countdown that ran out
     * meanwhile sends now.
     */
    static void resume(Context context) {
        Context app = context.getApplicationContext();
        SharedPreferences p = prefs(app);
        String phase = p.getString(KEY_PHASE, null);
        if (PHASE_COUNTDOWN.equals(phase)) {
            long sendsAt = p.getLong(KEY_SENDS_AT, 0L);
            // Ran out meanwhile: the alarm fires straight away, off this thread.
            long now = System.currentTimeMillis();
            schedule(app, ACTION_FIRE, Math.max(sendsAt, now + 1_000L));
            if (sendsAt > now) showCountdown(app, sendsAt);
        } else if (PHASE_SENDING.equals(phase)) {
            schedule(app, ACTION_RETRY, System.currentTimeMillis() + 1_000L);
        }
    }

    /** {pending, sendsAt}: sendsAt is when the countdown runs out, null once it has. */
    static boolean isPending(Context context) {
        return prefs(context).getString(KEY_PHASE, null) != null;
    }

    static Long countdownEndsAt(Context context) {
        SharedPreferences p = prefs(context);
        return PHASE_COUNTDOWN.equals(p.getString(KEY_PHASE, null)) ? p.getLong(KEY_SENDS_AT, 0L) : null;
    }

    /** A notification button or an alarm, from {@link CrashAlertReceiver}. Blocks: call off the main thread. */
    static void handle(Context context, String action) {
        Context app = context.getApplicationContext();
        if (ACTION_OK.equals(action)) {
            cancel(app);
            return;
        }
        boolean auto;
        synchronized (LOCK) {
            SharedPreferences p = prefs(app);
            String phase = p.getString(KEY_PHASE, null);
            if (ACTION_HELP.equals(action) || ACTION_FIRE.equals(action)) {
                // Only from the countdown: a send already under way is not started twice.
                if (!PHASE_COUNTDOWN.equals(phase)) return;
                auto = ACTION_FIRE.equals(action);
                p.edit()
                        .putString(KEY_PHASE, PHASE_SENDING)
                        .putBoolean(KEY_AUTO, auto)
                        .putLong(KEY_FIRST_TRY_AT, System.currentTimeMillis())
                        .commit();
            } else if (ACTION_RETRY.equals(action)) {
                if (!PHASE_SENDING.equals(phase)) return;
                auto = p.getBoolean(KEY_AUTO, true);
            } else {
                return;
            }
        }
        cancelAlarm(app, ACTION_FIRE);
        showSending(app);
        Outcome outcome = send(app, auto);
        Log.i(TAG, "crash SOS " + outcome);
        if (outcome == Outcome.SENT) {
            clear(app);
            notifyResult(app, "Your family was told",
                    "Jarvis sent your location to your family after the crash.");
        } else if (outcome == Outcome.REFUSED
                || System.currentTimeMillis() - prefs(app).getLong(KEY_FIRST_TRY_AT, 0L) >= RETRY_FOR_MS) {
            clear(app);
            notifyResult(app, "Could not reach your family - call someone",
                    "Jarvis could not send the crash alert. Call your family or emergency services.");
        } else {
            schedule(app, ACTION_RETRY, System.currentTimeMillis() + RETRY_EVERY_MS);
        }
    }

    /** "I'm OK": nothing is sent, the alarm and the notification go. */
    private static void cancel(Context app) {
        synchronized (LOCK) {
            // Once sending has begun the family may already know; the button
            // is gone from that notification, so this is only a late tap.
            if (!PHASE_COUNTDOWN.equals(prefs(app).getString(KEY_PHASE, null))) return;
            reportFalseAlarm(app);
            clear(app);
        }
        Log.i(TAG, "crash cancelled: the person is OK");
    }

    /**
     * The phone can tell the app's own activity feed this was a false alarm,
     * so a family can see the pattern (and, if alarms keep firing, have them
     * turned down). Nothing about where the person is goes anywhere; the
     * entry lands in the same feed the app already uses.
     */
    private static void reportFalseAlarm(Context app) {
        try {
            SharedPreferences p = prefs(app);
            JSONObject crash = new JSONObject(p.getString(KEY_CRASH, "{}"));
            JSONObject detail = new JSONObject();
            detail.put("at", crash.optLong("detected_at", System.currentTimeMillis()));
            detail.put("peak_g", crash.optDouble("peak_g", 0));
            detail.put("speed_before_mph", crash.optDouble("speed_before_mph", 0));
            long startedAt = p.getLong(KEY_SENDS_AT, 0L) - COUNTDOWN_MS;
            detail.put("answered_after_s", Math.max(0, (System.currentTimeMillis() - startedAt) / 1000));
            detail.put("diagnostics", withDevice(app, crash.optJSONObject("diagnostics")));
            report(app, "crash_false_alarm", detail);
        } catch (Exception e) {
            Log.w(TAG, "false alarm not recorded", e);
        }
    }

    /**
     * A hard hit the detector let go ({@link CrashDetector#takeIgnored}): kept
     * with the false alarms so the two can be compared.
     */
    static void reportIgnored(Context context, JSONObject diagnostics) {
        try {
            JSONObject detail = new JSONObject();
            detail.put("at", diagnostics.optLong("impact_at", System.currentTimeMillis()));
            detail.put("reason", diagnostics.optString("ignored", ""));
            detail.put("diagnostics", withDevice(context.getApplicationContext(), diagnostics));
            report(context.getApplicationContext(), "crash_candidate_ignored", detail);
        } catch (Exception e) {
            Log.w(TAG, "ignored hit not recorded", e);
        }
    }

    private static void report(Context app, String event, JSONObject detail) throws Exception {
        JSONObject body = new JSONObject();
        body.put("feature", "safety");
        body.put("event", event);
        body.put("item", "");
        body.put("detail", detail);
        ActivityOutbox.add(app, body);
    }

    /** The diagnostics with what this device is: its kind, model, Android and app build. */
    static JSONObject withDevice(Context app, JSONObject diagnostics) {
        JSONObject d = diagnostics != null ? diagnostics : new JSONObject();
        try {
            JSONObject device = new JSONObject();
            device.put("kind", DeviceNoun.of(app));
            device.put("model", Build.MANUFACTURER + " " + Build.MODEL);
            device.put("sdk", Build.VERSION.SDK_INT);
            try {
                PackageInfo pi = app.getPackageManager().getPackageInfo(app.getPackageName(), 0);
                device.put("app_build", Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
                        ? pi.getLongVersionCode() : pi.versionCode);
            } catch (Exception e) {
                device.put("app_build", JSONObject.NULL);
            }
            d.put("device", device);
        } catch (Exception e) {
            // Best effort.
        }
        return d;
    }

    private static void clear(Context app) {
        prefs(app).edit().clear().commit();
        cancelAlarm(app, ACTION_FIRE);
        cancelAlarm(app, ACTION_RETRY);
        NotificationManager nm = app.getSystemService(NotificationManager.class);
        if (nm != null) nm.cancel(NOTIFICATION_ID);
    }

    /**
     * One try. A network error, no credentials yet, a 429 or a 5xx is worth
     * another; any other 4xx would be refused again.
     */
    private static Outcome send(Context app, boolean auto) {
        try {
            SharedPreferences p = prefs(app);
            JSONObject crash = new JSONObject(p.getString(KEY_CRASH, "{}"));
            CrashDetector.Result result = CrashDetector.Result.fromJson(crash);
            JSONObject diagnostics = withDevice(app, result.diagnostics);
            if (!auto) {
                // "Get help" tapped: how far into the countdown.
                long startedAt = p.getLong(KEY_SENDS_AT, 0L) - COUNTDOWN_MS;
                diagnostics.put("answered_after_s",
                        Math.max(0, (p.getLong(KEY_FIRST_TRY_AT, System.currentTimeMillis()) - startedAt) / 1000));
            }
            result.diagnostics = diagnostics;
            String body = result.toBody(auto).toString();
            GuardApi.HttpResult r = GuardApi.send(app, "POST", "/api/family/crash", body);
            if (r == null) return Outcome.RETRY;
            return outcomeFor(r.status);
        } catch (Exception e) {
            Log.w(TAG, "crash SOS not sent: " + e.getMessage());
            return Outcome.RETRY;
        }
    }

    static Outcome outcomeFor(int status) {
        if (status >= 200 && status < 300) return Outcome.SENT;
        if (status >= 400 && status < 500 && status != 429) return Outcome.REFUSED;
        return Outcome.RETRY;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    // Alarms.

    private static PendingIntent alarmIntent(Context app, String action) {
        Intent i = new Intent(app, CrashAlertReceiver.class).setAction(action);
        return PendingIntent.getBroadcast(app, action.hashCode(), i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    /** Exact when allowed; allow-while-idle either way, so Doze does not hold it back. */
    private static void schedule(Context app, String action, long atWallMs) {
        AlarmManager am = (AlarmManager) app.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        PendingIntent pi = alarmIntent(app, action);
        try {
            boolean exact = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms();
            if (exact) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atWallMs, pi);
            } else {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atWallMs, pi);
            }
        } catch (SecurityException e) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atWallMs, pi);
        }
    }

    private static void cancelAlarm(Context app, String action) {
        AlarmManager am = (AlarmManager) app.getSystemService(Context.ALARM_SERVICE);
        if (am != null) am.cancel(alarmIntent(app, action));
    }

    // Notifications.

    /** Its own channel, so the person can find it in Settings and it is never quietened with chat. */
    private static void ensureChannel(Context app) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = app.getSystemService(NotificationManager.class);
        if (nm == null || nm.getNotificationChannel(CHANNEL_ID) != null) return;
        NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID, "Crash detection", NotificationManager.IMPORTANCE_HIGH);
        ch.setDescription("Asks if you are OK after a possible crash while driving, then tells your family.");
        ch.setSound(alarmSound(), new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build());
        ch.enableVibration(true);
        ch.setVibrationPattern(VIBRATION);
        ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
        nm.createNotificationChannel(ch);
    }

    private static final long[] VIBRATION = {0, 800, 400, 800, 400, 800};

    private static Uri alarmSound() {
        Uri u = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
        return u != null ? u : RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
    }

    private static PendingIntent actionIntent(Context app, String action) {
        Intent i = new Intent(app, CrashAlertReceiver.class).setAction(action);
        return PendingIntent.getBroadcast(app, action.hashCode() + 1, i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static PendingIntent openApp(Context app) {
        Intent i = new Intent(app, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(app, NOTIFICATION_ID, i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    /** Android 14 lets the person take full-screen alerts away from an app. */
    private static boolean mayUseFullScreen(Context app) {
        if (Build.VERSION.SDK_INT < 34) return true;
        NotificationManager nm = app.getSystemService(NotificationManager.class);
        return nm != null && nm.canUseFullScreenIntent();
    }

    private static void showCountdown(Context app, long sendsAt) {
        ensureChannel(app);
        NotificationCompat.Builder b = new NotificationCompat.Builder(app, CHANNEL_ID)
                .setSmallIcon(R.drawable.mi_warning)
                .setContentTitle("Did you crash?")
                .setContentText("Your family will be told when the countdown ends unless you are OK.")
                .setStyle(new NotificationCompat.BigTextStyle().bigText(
                        "It looks like you were in a crash. Your family will be sent your location "
                                + "when the countdown ends. Tap I'm OK if you are fine."))
                .setPriority(NotificationCompat.PRIORITY_MAX)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .setAutoCancel(false)
                .setWhen(sendsAt)
                .setShowWhen(true)
                .setUsesChronometer(true)
                .setChronometerCountDown(true)
                .setSound(alarmSound(), android.media.AudioManager.STREAM_ALARM)
                .setVibrate(VIBRATION)
                .setContentIntent(openApp(app))
                .addAction(new NotificationCompat.Action.Builder(0, "I'm OK", actionIntent(app, ACTION_OK)).build())
                .addAction(new NotificationCompat.Action.Builder(0, "Get help", actionIntent(app, ACTION_HELP)).build());
        if (mayUseFullScreen(app)) b.setFullScreenIntent(openApp(app), true);
        Notification n = b.build();
        // Keeps sounding until a button is tapped or the countdown ends.
        n.flags |= Notification.FLAG_INSISTENT;
        post(app, NOTIFICATION_ID, n);
    }

    private static void showSending(Context app) {
        ensureChannel(app);
        post(app, NOTIFICATION_ID, new NotificationCompat.Builder(app, CHANNEL_ID)
                .setSmallIcon(R.drawable.mi_warning)
                .setContentTitle("Telling your family")
                .setContentText("Sending your location after the crash.")
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setSilent(true)
                .setContentIntent(openApp(app))
                .build());
    }

    private static void notifyResult(Context app, String title, String text) {
        ensureChannel(app);
        post(app, RESULT_NOTIFICATION_ID, new NotificationCompat.Builder(app, CHANNEL_ID)
                .setSmallIcon(R.drawable.mi_warning)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(text))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_STATUS)
                .setAutoCancel(true)
                .setContentIntent(openApp(app))
                .build());
    }

    private static void post(Context app, int id, Notification n) {
        NotificationManager nm = app.getSystemService(NotificationManager.class);
        if (nm == null) return;
        try {
            nm.notify(id, n);
        } catch (SecurityException e) {
            // Notifications not allowed (Android 13+): the alarm still sends.
            Log.w(TAG, "notification not allowed", e);
        }
    }
}
