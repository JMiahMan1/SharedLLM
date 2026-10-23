package com.jarvisos.app;

import android.app.AppOpsManager;
import android.app.usage.UsageEvents;
import android.app.usage.UsageStats;
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

import org.json.JSONObject;

import java.util.Calendar;
import java.util.List;

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

        long millis = 0;
        UsageWindow window = new UsageWindow();
        if (manager != null) {
            List<UsageStats> stats =
                    manager.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, start, now);
            if (stats != null) {
                for (UsageStats day : stats) {
                    if (day.getLastTimeUsed() >= start) {
                        millis += day.getTotalTimeInForeground();
                    }
                }
            }
            UsageEvents events = manager.queryEvents(start, now);
            if (events != null) {
                UsageEvents.Event event = new UsageEvents.Event();
                while (events.getNextEvent(event)) {
                    window.accept(event.getEventType(), event.getTimeStamp());
                }
            }
        }

        ret.put("millis", Math.max(0, millis));
        ret.put("firstUse", window.hasData() ? window.firstUse : JSONObject.NULL);
        ret.put("lastUse", window.hasData() ? window.lastUse : JSONObject.NULL);
        ret.put("sessions", window.sessions);
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
