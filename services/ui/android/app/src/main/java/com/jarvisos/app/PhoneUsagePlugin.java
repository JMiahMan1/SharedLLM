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
        JSObject ret = new JSObject();
        long now = System.currentTimeMillis();
        long start = startOfDay(now);
        Context context = bridge.getContext();
        UsageStatsManager manager =
                (UsageStatsManager) context.getSystemService(Context.USAGE_STATS_SERVICE);

        UsageWindow window = new UsageWindow();
        // The day total comes from the event stream, which is clipped to
        // [start, now]. queryUsageStats(INTERVAL_DAILY, start, now) is not: the
        // system expands a daily query to whole-day buckets, so a bucket's
        // getTotalTimeInForeground() can include last night's foreground time and
        // was being counted as "screen time today". Summing RESUMED -> PAUSED
        // stretches from the clipped event stream cannot do that.
        ForegroundTime foreground = new ForegroundTime();
        if (manager != null) {
            UsageEvents events = manager.queryEvents(start, now);
            if (events != null) {
                UsageEvents.Event event = new UsageEvents.Event();
                while (events.getNextEvent(event)) {
                    window.accept(event.getEventType(), event.getTimeStamp());
                    foreground.accept(event.getEventType(), event.getTimeStamp(), event.getPackageName());
                }
            }
            foreground.finish(now);
        }
        long millis = foreground.totalMillis();

        // Per-app foreground time for the day, most used first, top twenty so a
        // busy day does not ship hundreds of near-zero rows.
        java.util.ArrayList<String> usedPackages = new java.util.ArrayList<>(foreground.packages());
        usedPackages.sort((a, b) -> Long.compare(foreground.millisFor(b), foreground.millisFor(a)));
        JSONArray apps = new JSONArray();
        int limit = Math.min(20, usedPackages.size());
        for (int i = 0; i < limit; i++) {
            String packageName = usedPackages.get(i);
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
        call.resolve(ret);
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
        JSONArray intervals = new JSONArray();
        UsageStatsManager manager =
                (UsageStatsManager) bridge.getContext().getSystemService(Context.USAGE_STATS_SERVICE);
        if (manager != null && hasUsageAccess() && since < until) {
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
        JSObject ret = new JSObject();
        ret.put("since", since);
        ret.put("until", until);
        ret.put("intervals", intervals);
        call.resolve(ret);
    }

    private boolean hasUsageAccess() {
        try {
            AppOpsManager appOps =
                    (AppOpsManager) bridge.getContext().getSystemService(Context.APP_OPS_SERVICE);
            if (appOps == null) {
                return false;
            }
            int mode =
                    appOps.checkOpNoThrow(
                            AppOpsManager.OPSTR_GET_USAGE_STATS,
                            Process.myUid(),
                            bridge.getContext().getPackageName());
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
