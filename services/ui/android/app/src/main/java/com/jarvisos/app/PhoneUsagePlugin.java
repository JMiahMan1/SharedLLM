package com.jarvisos.app;

import android.app.AppOpsManager;
import android.app.usage.UsageEvents;
import android.app.usage.UsageStatsManager;
import android.content.Context;
import android.content.Intent;
import android.os.Process;
import android.provider.Settings;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Calendar;

/**
 * General phone usage: how long the screen was used today, and when the day's
 * use started and stopped. Backed by the system's usage access (the special
 * Settings permission), which also lets the profile see per-app detail later.
 */
@CapacitorPlugin(name = "PhoneUsage")
public class PhoneUsagePlugin extends Plugin {
    static final int MAX_APPS = 60;
    static final long MIN_APP_MILLIS = 60_000L;

    @PluginMethod
    public void hasAccess(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("granted", hasUsageAccess());
        call.resolve(ret);
    }

    @PluginMethod
    public void openSettings(PluginCall call) {
        Intent intent = new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        bridge.getContext().startActivity(intent);
        call.resolve();
    }

    @PluginMethod
    public void getToday(PluginCall call) {
        long now = System.currentTimeMillis();
        call.resolve(readDay(bridge.getContext(), startOfDay(now), now));
    }

    /**
     * The phone's use between {@code start} and {@code end} (epoch ms): screen
     * time, when use started and stopped, sessions, and the top apps. The
     * plugin reads today with it, and {@link UsageSyncWorker} reads today and
     * the finished yesterday while the app is closed.
     */
    static JSObject readDay(Context context, long start, long end) {
        JSObject ret = new JSObject();
        UsageStatsManager manager =
                (UsageStatsManager) context.getSystemService(Context.USAGE_STATS_SERVICE);

        UsageWindow window = new UsageWindow();
        // The day total comes from the event stream, which is clipped to
        // [start, end]. queryUsageStats(INTERVAL_DAILY, start, end) is not: the
        // system expands a daily query to whole-day buckets, so a bucket's
        // getTotalTimeInForeground() can include last night's foreground time and
        // was being counted as "screen time today". Summing RESUMED -> PAUSED
        // stretches from the clipped event stream cannot do that.
        ForegroundTime foreground = new ForegroundTime();
        if (manager != null) {
            UsageEvents events = manager.queryEvents(start, end);
            if (events != null) {
                UsageEvents.Event event = new UsageEvents.Event();
                while (events.getNextEvent(event)) {
                    window.accept(event.getEventType(), event.getTimeStamp());
                    foreground.accept(event.getEventType(), event.getTimeStamp(), event.getPackageName());
                }
            }
            foreground.finish(end);
        }
        long millis = foreground.totalMillis();

        // Per-app foreground time for the day, most used first: every app used
        // for at least a minute (up to MAX_APPS), so a parent's limit on an app
        // that is not among the busiest still sees its time, while a busy day
        // does not ship hundreds of near-zero rows.
        java.util.ArrayList<String> usedPackages = new java.util.ArrayList<>(foreground.packages());
        usedPackages.sort((a, b) -> Long.compare(foreground.millisFor(b), foreground.millisFor(a)));
        JSONArray apps = new JSONArray();
        int limit = Math.min(MAX_APPS, usedPackages.size());
        for (int i = 0; i < limit; i++) {
            String packageName = usedPackages.get(i);
            if (foreground.millisFor(packageName) < MIN_APP_MILLIS) break;
            JSObject app = new JSObject();
            app.put("package", packageName);
            app.put("millis", foreground.millisFor(packageName));
            Long lastUse = foreground.lastUseFor(packageName);
            app.put("lastUse", lastUse == null ? JSONObject.NULL : lastUse);
            apps.put(app);
        }

        ret.put("millis", Math.max(0, millis));
        ret.put("firstUse", window.hasData() ? window.firstUse : JSONObject.NULL);
        ret.put("lastUse", window.hasData() ? window.lastUse : JSONObject.NULL);
        ret.put("sessions", window.sessions);
        ret.put("apps", apps);
        return ret;
    }

    /**
     * The stretches the phone was in use between ``since`` and ``until``
     * (epoch ms; the web side asks for 18:00 the evening before to now, up to
     * noon), for the sleep estimate. Times only, no app names.
     */
    @PluginMethod
    public void getNight(PluginCall call) {
        long now = System.currentTimeMillis();
        long until = Math.min(call.getLong("until", now), now);
        long since = call.getLong("since", until - 18L * 60 * 60 * 1000);
        JSObject ret = new JSObject();
        ret.put("since", since);
        ret.put("until", until);
        ret.put("intervals", readNight(bridge.getContext(), since, until));
        call.resolve(ret);
    }

    /** The screen-on stretches between {@code since} and {@code until}, as [start, end] pairs. */
    static JSONArray readNight(Context context, long since, long until) {
        JSONArray intervals = new JSONArray();
        UsageStatsManager manager =
                (UsageStatsManager) context.getSystemService(Context.USAGE_STATS_SERVICE);
        if (manager != null && hasUsageAccess(context) && since < until) {
            NightUse night = new NightUse();
            UsageEvents events = manager.queryEvents(since, until);
            if (events != null) {
                UsageEvents.Event event = new UsageEvents.Event();
                while (events.getNextEvent(event)) {
                    night.accept(event.getEventType(), event.getTimeStamp());
                }
            }
            for (long[] stretch : night.finish(until)) {
                JSONArray pair = new JSONArray();
                pair.put(stretch[0]);
                pair.put(stretch[1]);
                intervals.put(pair);
            }
        }
        return intervals;
    }

    private boolean hasUsageAccess() {
        return hasUsageAccess(bridge.getContext());
    }

    static boolean hasUsageAccess(Context context) {
        try {
            AppOpsManager appOps =
                    (AppOpsManager) context.getSystemService(Context.APP_OPS_SERVICE);
            if (appOps == null) {
                return false;
            }
            int mode =
                    appOps.checkOpNoThrow(
                            AppOpsManager.OPSTR_GET_USAGE_STATS,
                            Process.myUid(),
                            context.getPackageName());
            return mode == AppOpsManager.MODE_ALLOWED;
        } catch (Exception e) {
            return false;
        }
    }

    /** Midnight of the current local day, in epoch millis. */
    static long startOfDay(long timestamp) {
        Calendar calendar = Calendar.getInstance();
        calendar.setTimeInMillis(timestamp);
        calendar.set(Calendar.HOUR_OF_DAY, 0);
        calendar.set(Calendar.MINUTE, 0);
        calendar.set(Calendar.SECOND, 0);
        calendar.set(Calendar.MILLISECOND, 0);
        return calendar.getTimeInMillis();
    }
}
