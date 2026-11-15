package com.jarvisos.app;

import android.app.usage.StorageStats;
import android.app.usage.StorageStatsManager;
import android.app.usage.UsageStats;
import android.app.usage.UsageStatsManager;
import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.InstallSourceInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Process;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * The apps a person installed on this device, for app vetting on a child's
 * device (docs/APP_VETTING.md): package, name, which store installed it,
 * first install and last update, whether it opens from the launcher, the
 * dangerous permissions it asks for, when it was last used and its size.
 *
 * The web app sends this only from a child's or teen's device (the server
 * refuses anyone else's). Last use and size need usage access; without it
 * they are left out (null), never guessed.
 */
final class AppInventory {

    /** How far back last use is read: Android keeps about two years of usage history. */
    static final long HISTORY_MS = 2L * 365 * 24 * 60 * 60 * 1000;

    private AppInventory() {}

    static JSONArray collect(Context context) {
        PackageManager pm = context.getPackageManager();
        long now = System.currentTimeMillis();
        boolean usageAccess = PhoneUsagePlugin.hasUsageAccess(context);
        Map<String, UsageStats> usage = usageAccess ? usage(context, now) : Collections.emptyMap();
        JSONArray out = new JSONArray();
        List<PackageInfo> packages = pm.getInstalledPackages(PackageManager.GET_PERMISSIONS);
        for (PackageInfo info : packages) {
            ApplicationInfo app = info.applicationInfo;
            if (app == null || !AppInventoryRules.listed(info.packageName, app.flags, context.getPackageName())) continue;
            try {
                JSONObject row = new JSONObject();
                row.put("package", info.packageName);
                row.put("label", String.valueOf(pm.getApplicationLabel(app)));
                row.put("installer", installer(pm, info.packageName));
                row.put("first_install", info.firstInstallTime);
                row.put("last_update", info.lastUpdateTime);
                row.put("launchable", pm.getLaunchIntentForPackage(info.packageName) != null);
                JSONArray groups = new JSONArray();
                List<String> requested = info.requestedPermissions == null
                        ? Collections.emptyList() : Arrays.asList(info.requestedPermissions);
                for (String group : AppInventoryRules.groups(requested)) groups.put(group);
                row.put("permissions", groups);
                UsageStats stats = usage.get(info.packageName);
                Long lastUsed = stats == null ? null
                        : AppInventoryRules.lastUsed(stats.getLastTimeUsed(), info.firstInstallTime, now);
                row.put("last_used", lastUsed == null ? JSONObject.NULL : lastUsed);
                Long size = usageAccess ? size(context, app) : null;
                row.put("size_bytes", size == null ? JSONObject.NULL : size);
                out.put(row);
            } catch (Exception ignored) {
                // One app that cannot be read is left out; the rest still report.
            }
        }
        return out;
    }

    private static Map<String, UsageStats> usage(Context context, long now) {
        UsageStatsManager manager = (UsageStatsManager) context.getSystemService(Context.USAGE_STATS_SERVICE);
        if (manager == null) return Collections.emptyMap();
        try {
            Map<String, UsageStats> stats = manager.queryAndAggregateUsageStats(now - HISTORY_MS, now);
            return stats == null ? Collections.emptyMap() : stats;
        } catch (Exception e) {
            return Collections.emptyMap();
        }
    }

    /** The store that installed the app (Play, Galaxy Store...), or null for a sideload or adb. */
    private static Object installer(PackageManager pm, String packageName) {
        try {
            InstallSourceInfo source = pm.getInstallSourceInfo(packageName);
            String name = source.getInstallingPackageName();
            return name == null ? JSONObject.NULL : name;
        } catch (Exception e) {
            return JSONObject.NULL;
        }
    }

    /** App, data and cache bytes (needs usage access), or null when Android will not say. */
    private static Long size(Context context, ApplicationInfo app) {
        try {
            StorageStatsManager manager = context.getSystemService(StorageStatsManager.class);
            if (manager == null) return null;
            StorageStats stats = manager.queryStatsForPackage(app.storageUuid, app.packageName, Process.myUserHandle());
            return stats.getAppBytes() + stats.getDataBytes();
        } catch (Exception e) {
            return null;
        }
    }
}
