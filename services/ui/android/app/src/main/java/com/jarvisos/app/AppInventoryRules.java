package com.jarvisos.app;

import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
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
