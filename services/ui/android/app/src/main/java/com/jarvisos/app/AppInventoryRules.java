package com.jarvisos.app;

import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * App vetting (docs/APP_VETTING.md): which installed apps a child's device
 * reports, and which of the permissions they ask for count. Plain Java with
 * no Android types, so every case is unit-tested on the JVM;
 * {@link AppInventory} reads the device and applies these rules.
 *
 * Only apps a person installed are listed (no system apps, no updated
 * system apps) and never Jarvis itself. Permissions are reported as the
 * server's groups (location, camera, microphone, contacts, sms, phone,
 * photos, calendar, nearby): the same names the guard's permission requests
 * use, so a parent reads one vocabulary everywhere.
 *
 * The security scan's inputs are decided here too: the risky capabilities an
 * app declares or asks for (services/common/app_security.py CAPABILITIES),
 * which components a "pkg/cls:pkg/cls" settings list switches on, the hex of
 * a certificate fingerprint, and whether an app file is hashed now, later or
 * never (APK_MAX_BYTES, HASH_BUDGET_BYTES).
 */
public final class AppInventoryRules {

    /** ApplicationInfo.FLAG_SYSTEM and FLAG_UPDATED_SYSTEM_APP (constant values, so this stays plain Java). */
    static final int FLAG_SYSTEM = 1;
    static final int FLAG_UPDATED_SYSTEM_APP = 1 << 7;

    /** Android's dangerous permissions, by the group a parent reads. */
    private static final Map<String, String> GROUPS = new HashMap<>();

    static {
        group("location", "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION");
        group("camera", "CAMERA");
        group("microphone", "RECORD_AUDIO");
        group("contacts", "READ_CONTACTS", "WRITE_CONTACTS", "GET_ACCOUNTS");
        group("sms", "READ_SMS", "SEND_SMS", "RECEIVE_SMS", "RECEIVE_MMS", "RECEIVE_WAP_PUSH");
        group("phone", "READ_CALL_LOG", "WRITE_CALL_LOG", "CALL_PHONE", "READ_PHONE_NUMBERS", "PROCESS_OUTGOING_CALLS",
                "ANSWER_PHONE_CALLS");
        group("photos", "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "READ_MEDIA_VISUAL_USER_SELECTED",
                "READ_EXTERNAL_STORAGE");
        group("calendar", "READ_CALENDAR", "WRITE_CALENDAR");
        group("nearby", "BLUETOOTH_SCAN", "BLUETOOTH_CONNECT", "BLUETOOTH_ADVERTISE", "NEARBY_WIFI_DEVICES",
                "UWB_RANGING");
    }

    /** Fine-grained permissions behind the risky capabilities the server knows. */
    private static final Map<String, String> CAPABILITY_PERMISSIONS = new HashMap<>();

    static {
        CAPABILITY_PERMISSIONS.put("android.permission.READ_SMS", "sms_read");
        CAPABILITY_PERMISSIONS.put("android.permission.RECEIVE_SMS", "sms_read");
        CAPABILITY_PERMISSIONS.put("android.permission.SEND_SMS", "sms_send");
        CAPABILITY_PERMISSIONS.put("android.permission.READ_CALL_LOG", "call_log");
        CAPABILITY_PERMISSIONS.put("android.permission.ACCESS_BACKGROUND_LOCATION", "background_location");
        CAPABILITY_PERMISSIONS.put("android.permission.SYSTEM_ALERT_WINDOW", "overlay");
        CAPABILITY_PERMISSIONS.put("android.permission.REQUEST_INSTALL_PACKAGES", "install_apps");
    }

    /** Components an app declares, by the capability they give it (bound only by the system). */
    public static final String DECLARES_ACCESSIBILITY = "accessibility";
    public static final String DECLARES_DEVICE_ADMIN = "device_admin";
    public static final String DECLARES_NOTIFICATION_LISTENER = "notification_listener";
    public static final String DECLARES_VPN = "vpn";

    /** The permission a declared component must be guarded by to count (the system's bind permission). */
    private static final Map<String, String> BIND_PERMISSIONS = new HashMap<>();

    static {
        BIND_PERMISSIONS.put(DECLARES_ACCESSIBILITY, "android.permission.BIND_ACCESSIBILITY_SERVICE");
        BIND_PERMISSIONS.put(DECLARES_DEVICE_ADMIN, "android.permission.BIND_DEVICE_ADMIN");
        BIND_PERMISSIONS.put(DECLARES_NOTIFICATION_LISTENER, "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE");
        BIND_PERMISSIONS.put(DECLARES_VPN, "android.permission.BIND_VPN_SERVICE");
    }

    /** App files larger than this are not hashed (reported as "too_large"). */
    public static final long APK_MAX_BYTES = 300L * 1024 * 1024;
    /** Bytes hashed per report at most; the rest wait for a later report ("deferred"). */
    public static final long HASH_BUDGET_BYTES = 1024L * 1024 * 1024;
    /** The device's answer for an app file: hash it now, too large, or wait for a later report. */
    public static final String HASH_NOW = "hash";
    public static final String HASH_TOO_LARGE = "too_large";
    public static final String HASH_DEFERRED = "deferred";
    /** At most this many signing certificates are reported per app. */
    public static final int MAX_CERTS = 4;

    private AppInventoryRules() {}

    private static void group(String name, String... permissions) {
        for (String permission : permissions) GROUPS.put("android.permission." + permission, name);
    }

    /** Whether an app was installed by a person (not part of the system image). */
    public static boolean isUserApp(int flags) {
        return (flags & (FLAG_SYSTEM | FLAG_UPDATED_SYSTEM_APP)) == 0;
    }

    /** Whether the device lists this package: a person's app, and never Jarvis itself. */
    public static boolean listed(String packageName, int flags, String ownPackage) {
        return packageName != null && !packageName.isEmpty() && !packageName.equals(ownPackage) && isUserApp(flags);
    }

    /** The parent-facing groups of the dangerous permissions an app asks for, sorted. */
    public static Set<String> groups(Collection<String> requested) {
        if (requested == null || requested.isEmpty()) return Collections.emptySet();
        Set<String> out = new TreeSet<>();
        for (String permission : requested) {
            String group = permission == null ? null : GROUPS.get(permission);
            if (group != null) out.add(group);
        }
        return out;
    }

    /**
     * The risky capabilities an app declares or asks for, sorted: from its
     * requested permissions and the system components it declares (each
     * {@code DECLARES_*} name in {@code declared}).
     */
    public static Set<String> capabilities(Collection<String> requested, Collection<String> declared) {
        Set<String> out = new TreeSet<>();
        if (requested != null) {
            for (String permission : requested) {
                String capability = permission == null ? null : CAPABILITY_PERMISSIONS.get(permission);
                if (capability != null) out.add(capability);
            }
        }
        if (declared != null) {
            for (String name : declared) {
                if (name != null && BIND_PERMISSIONS.containsKey(name)) out.add(name);
            }
        }
        return out;
    }

    /** Whether a declared component counts: it is guarded by the system's bind permission for that kind. */
    public static boolean declares(String kind, String componentPermission) {
        String expected = BIND_PERMISSIONS.get(kind);
        return expected != null && expected.equals(componentPermission);
    }

    /**
     * The packages a settings list of components names ("pkg/cls:pkg/.cls",
     * as Android keeps enabled accessibility services and notification
     * listeners). Blank or malformed parts are skipped.
     */
    public static Set<String> packagesIn(String componentList) {
        if (componentList == null || componentList.trim().isEmpty()) return Collections.emptySet();
        Set<String> out = new HashSet<>();
        for (String part : componentList.split(":")) {
            int slash = part.indexOf('/');
            String pkg = (slash > 0 ? part.substring(0, slash) : "").trim();
            if (!pkg.isEmpty()) out.add(pkg);
        }
        return out;
    }

    /** Uppercase hex of a digest (how the stalkerware list writes certificate fingerprints). */
    public static String hex(byte[] digest) {
        StringBuilder out = new StringBuilder(digest.length * 2);
        for (byte b : digest) out.append(String.format(Locale.ROOT, "%02X", b));
        return out.toString();
    }

    /** What to do with an app file of {@code size} bytes when {@code budgetLeft} bytes may still be hashed. */
    public static String hashPlan(long size, long budgetLeft) {
        if (size > APK_MAX_BYTES) return HASH_TOO_LARGE;
        if (size > budgetLeft) return HASH_DEFERRED;
        return HASH_NOW;
    }

    /**
     * A cached hash is good for exactly one version of an app: the cache keeps
     * "lastUpdateTime|sha256" per package and this reads it back, or null when
     * the app was updated since (or the entry is malformed).
     */
    public static String cachedHash(String cached, long lastUpdate) {
        if (cached == null) return null;
        int bar = cached.indexOf('|');
        if (bar <= 0) return null;
        String sha = cached.substring(bar + 1);
        if (!cached.substring(0, bar).equals(Long.toString(lastUpdate)) || !sha.matches("[0-9a-f]{64}")) return null;
        return sha;
    }

    /** The cache entry for one version of an app file. */
    public static String cacheEntry(long lastUpdate, String sha256) {
        return lastUpdate + "|" + sha256;
    }

    /**
     * A usage-stats "last used" time worth reporting: Android answers 0, or a
     * time before the app was installed, for an app never opened. Returns
     * null for those, never a made-up time.
     */
    public static Long lastUsed(long lastTimeUsed, long firstInstall, long now) {
        if (lastTimeUsed <= 0 || lastTimeUsed > now + 60_000L) return null;
        if (firstInstall > 0 && lastTimeUsed < firstInstall) return null;
        return lastTimeUsed;
    }
}
