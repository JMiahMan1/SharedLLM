package com.jarvisos.app;

import android.app.admin.DevicePolicyManager;
import android.app.usage.StorageStats;
import android.app.usage.StorageStatsManager;
import android.app.usage.UsageStats;
import android.app.usage.UsageStatsManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.InstallSourceInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.content.pm.Signature;
import android.content.pm.SigningInfo;
import android.os.Process;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The apps a person installed on this device, for app vetting on a child's
 * device (docs/APP_VETTING.md): package, name, which store installed it,
 * first install and last update, whether it opens from the launcher, the
 * dangerous permissions it asks for, when it was last used and its size.
 *
 * The web app sends this only from a child's or teen's device (the server
 * refuses anyone else's). Last use and size need usage access; without it
 * they are left out (null), never guessed.
 *
 * For the security scan each row also carries the risky capabilities the app
 * declares or asks for, the special access switched on for it now
 * (accessibility, device administration, reading notifications), the SHA-1
 * of its signing certificates, and the SHA-256 of its base APK. The hash is
 * computed once per app version (cached by lastUpdateTime in
 * {@link #HASH_PREFS}); a file over {@link AppInventoryRules#APK_MAX_BYTES} is
 * skipped as "too_large", and at most {@link AppInventoryRules#HASH_BUDGET_BYTES}
 * are read per report ("deferred" until a later one).
 */
final class AppInventory {

    /** How far back last use is read: Android keeps about two years of usage history. */
    static final long HISTORY_MS = 2L * 365 * 24 * 60 * 60 * 1000;

    /** Where app file hashes are cached: package -> "lastUpdateTime|sha256". */
    static final String HASH_PREFS = "app_vetting_apk_hashes";

    private AppInventory() {}

    static JSONArray collect(Context context) {
        Scan scan = new Scan(context);
        JSONArray out = new JSONArray();
        Set<String> listed = new HashSet<>();
        for (PackageInfo info : scan.packages()) {
            JSONObject row = scan.row(info, listed);
            if (row != null) out.put(row);
        }
        scan.forgetUnlisted(listed);
        return out;
    }

    /**
     * One app's profile, for the request a parent answers before the app is
     * allowed to open (same fields a report's row carries, so the server reads
     * the profile and the inventory the same way). Null when the app cannot be
     * read at all: the request then goes without it rather than with a guess.
     */
    static JSONObject profile(Context context, String packageName) {
        try {
            PackageInfo info = context.getPackageManager().getPackageInfo(
                    packageName, PackageManager.GET_PERMISSIONS | PackageManager.GET_SIGNING_CERTIFICATES);
            return new Scan(context).row(info, null);
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * What is read once for a whole report: the installed list, usage, the
     * declared components, the special access switched on, and the hash cache
     * and budget. A single app's profile uses the same object, so a profile and
     * a report never disagree about the same app.
     */
    private static final class Scan {
        private final Context context;
        private final PackageManager pm;
        private final Map<String, UsageStats> usage;
        private final Map<String, Set<String>> declared;
        private final Set<String> accessibilityOn;
        private final Set<String> listenersOn;
        private final Set<String> adminsOn;
        private final SharedPreferences hashes;
        private final SharedPreferences.Editor hashEdits;
        private final long[] budget = {AppInventoryRules.HASH_BUDGET_BYTES};

        Scan(Context context) {
            this.context = context;
            this.pm = context.getPackageManager();
            long now = System.currentTimeMillis();
            this.usage = PhoneUsagePlugin.hasUsageAccess(context)
                    ? AppInventory.usage(context, now) : Collections.emptyMap();
            this.declared = declaredComponents(pm);
            this.accessibilityOn = AppInventoryRules.packagesIn(
                    secure(context, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES));
            this.listenersOn = AppInventoryRules.packagesIn(secure(context, "enabled_notification_listeners"));
            this.adminsOn = activeAdmins(context);
            this.hashes = context.getSharedPreferences(HASH_PREFS, Context.MODE_PRIVATE);
            this.hashEdits = hashes.edit();
        }

        List<PackageInfo> packages() {
            return pm.getInstalledPackages(PackageManager.GET_PERMISSIONS | PackageManager.GET_SIGNING_CERTIFICATES);
        }

        /** One app's row, or null when it is not an app this device reports. */
        JSONObject row(PackageInfo info, Set<String> listed) {
            ApplicationInfo app = info.applicationInfo;
            if (app == null || !AppInventoryRules.listed(info.packageName, app.flags, context.getPackageName())) {
                return null;
            }
            long now = System.currentTimeMillis();
            try {
                JSONObject row = new JSONObject();
                row.put("package", info.packageName);
                row.put("label", String.valueOf(pm.getApplicationLabel(app)));
                row.put("installer", installer(pm, info.packageName));
                row.put("first_install", info.firstInstallTime);
                row.put("last_update", info.lastUpdateTime);
                row.put("version_name", String.valueOf(info.versionName));
                row.put("version_code", info.getLongVersionCode());
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
                Long size = usageAccess() ? size(context, app) : null;
                row.put("size_bytes", size == null ? JSONObject.NULL : size);

                Set<String> mine = declared.containsKey(info.packageName)
                        ? declared.get(info.packageName) : Collections.emptySet();
                JSONArray caps = new JSONArray();
                for (String cap : AppInventoryRules.capabilities(requested, mine)) caps.put(cap);
                row.put("capabilities", caps);
                JSONArray special = new JSONArray();
                if (accessibilityOn.contains(info.packageName)) special.put(AppInventoryRules.DECLARES_ACCESSIBILITY);
                if (adminsOn.contains(info.packageName)) special.put(AppInventoryRules.DECLARES_DEVICE_ADMIN);
                if (listenersOn.contains(info.packageName)) special.put(AppInventoryRules.DECLARES_NOTIFICATION_LISTENER);
                row.put("special_access", special);
                row.put("cert_sha1", certificates(info.signingInfo));
                if (listed != null) listed.add(info.packageName);
                apkHash(row, app, info, hashes, hashEdits, budget);
                return row;
            } catch (Exception ignored) {
                // One app that cannot be read is left out; the rest still report.
                return null;
            }
        }

        /** A cached hash whose app is gone is dropped, so the cache cannot grow forever. */
        void forgetUnlisted(Set<String> listed) {
            for (String cachedPackage : hashes.getAll().keySet()) {
                if (!listed.contains(cachedPackage)) hashEdits.remove(cachedPackage);
            }
            hashEdits.apply();
        }

        private boolean usageAccess() {
            return PhoneUsagePlugin.hasUsageAccess(context);
        }
    }

    /** Each package's declared system components (accessibility, notification listener, VPN, device admin). */
    private static Map<String, Set<String>> declaredComponents(PackageManager pm) {
        Map<String, Set<String>> out = new HashMap<>();
        String[][] services = {
            {AppInventoryRules.DECLARES_ACCESSIBILITY, "android.accessibilityservice.AccessibilityService"},
            {AppInventoryRules.DECLARES_NOTIFICATION_LISTENER, "android.service.notification.NotificationListenerService"},
            {AppInventoryRules.DECLARES_VPN, "android.net.VpnService"},
        };
        for (String[] kind : services) {
            try {
                for (ResolveInfo info : pm.queryIntentServices(new Intent(kind[1]), 0)) {
                    if (info.serviceInfo != null && AppInventoryRules.declares(kind[0], info.serviceInfo.permission)) {
                        out.computeIfAbsent(info.serviceInfo.packageName, k -> new HashSet<>()).add(kind[0]);
                    }
                }
            } catch (Exception ignored) {
                // A query Android refuses leaves that kind out; the rest still report.
            }
        }
        try {
            Intent admin = new Intent("android.app.action.DEVICE_ADMIN_ENABLED");
            for (ResolveInfo info : pm.queryBroadcastReceivers(admin, 0)) {
                if (info.activityInfo != null
                        && AppInventoryRules.declares(AppInventoryRules.DECLARES_DEVICE_ADMIN, info.activityInfo.permission)) {
                    out.computeIfAbsent(info.activityInfo.packageName, k -> new HashSet<>())
                            .add(AppInventoryRules.DECLARES_DEVICE_ADMIN);
                }
            }
        } catch (Exception ignored) {
            // As above.
        }
        return out;
    }

    private static String secure(Context context, String name) {
        try {
            return Settings.Secure.getString(context.getContentResolver(), name);
        } catch (Exception e) {
            return null;
        }
    }

    private static Set<String> activeAdmins(Context context) {
        Set<String> out = new HashSet<>();
        try {
            DevicePolicyManager dpm = context.getSystemService(DevicePolicyManager.class);
            List<ComponentName> admins = dpm == null ? null : dpm.getActiveAdmins();
            if (admins != null) for (ComponentName admin : admins) out.add(admin.getPackageName());
        } catch (Exception ignored) {
            // Unknown: reported as not switched on, never guessed.
        }
        return out;
    }

    /** SHA-1 of each signing certificate (current signers, or the rotation history), uppercase hex. */
    private static JSONArray certificates(SigningInfo signing) {
        JSONArray out = new JSONArray();
        if (signing == null) return out;
        try {
            Signature[] signatures = signing.hasMultipleSigners()
                    ? signing.getApkContentsSigners() : signing.getSigningCertificateHistory();
            if (signatures == null) return out;
            for (Signature signature : signatures) {
                if (out.length() >= AppInventoryRules.MAX_CERTS) break;
                out.put(AppInventoryRules.hex(MessageDigest.getInstance("SHA-1").digest(signature.toByteArray())));
            }
        } catch (Exception ignored) {
            // No fingerprints: the package name still matches.
        }
        return out;
    }

    /** The base APK's SHA-256, from the cache when this version was hashed before. */
    private static void apkHash(JSONObject row, ApplicationInfo app, PackageInfo info, SharedPreferences cache,
                                SharedPreferences.Editor edits, long[] budget) throws Exception {
        String cached = AppInventoryRules.cachedHash(cache.getString(info.packageName, null), info.lastUpdateTime);
        if (cached != null) {
            row.put("apk_sha256", cached);
            row.put("apk_hash_state", "ok");
            return;
        }
        File apk = app.sourceDir == null ? null : new File(app.sourceDir);
        if (apk == null || !apk.canRead()) {
            row.put("apk_sha256", JSONObject.NULL);
            row.put("apk_hash_state", "unreadable");
            return;
        }
        String plan = AppInventoryRules.hashPlan(apk.length(), budget[0]);
        if (!AppInventoryRules.HASH_NOW.equals(plan)) {
            row.put("apk_sha256", JSONObject.NULL);
            row.put("apk_hash_state", plan);
            return;
        }
        budget[0] -= apk.length();
        try (InputStream in = new FileInputStream(apk)) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) digest.update(buffer, 0, read);
            String sha = AppInventoryRules.hex(digest.digest()).toLowerCase(Locale.ROOT);
            edits.putString(info.packageName, AppInventoryRules.cacheEntry(info.lastUpdateTime, sha));
            row.put("apk_sha256", sha);
            row.put("apk_hash_state", "ok");
        } catch (Exception e) {
            row.put("apk_sha256", JSONObject.NULL);
            row.put("apk_hash_state", "unreadable");
        }
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
            String name;
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
                InstallSourceInfo source = pm.getInstallSourceInfo(packageName);
                name = source.getInstallingPackageName();
            } else {
                name = pm.getInstallerPackageName(packageName);
            }
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
