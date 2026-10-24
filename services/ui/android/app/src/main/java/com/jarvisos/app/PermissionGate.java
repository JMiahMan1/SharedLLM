package com.jarvisos.app;

import java.util.Arrays;
import java.util.Collection;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Per-permission gating (Phase 11 step 5, Family Link's app permissions): a
 * parent chooses kinds of permission (camera, location, ...) that an app on
 * the child's phone may only be given once a parent approves it for that app.
 *
 * Without device-owner rights the guard cannot change a permission itself, so
 * it watches Android's own "Allow ... to ...?" grant dialog: when the dialog
 * asks for a gated kind the app has no approval for, the dialog is dismissed
 * (which denies it) and the parents are asked. The kind is read from the
 * dialog's English text, so another phone language is not recognised.
 *
 * Plain Java, JVM-tested.
 */
public final class PermissionGate {

    /** The apps that show Android's runtime permission dialog. */
    static final Set<String> CONTROLLERS = new HashSet<>(Arrays.asList(
            "com.google.android.permissioncontroller",
            "com.android.permissioncontroller",
            "com.google.android.packageinstaller",
            "com.android.packageinstaller",
            "com.samsung.android.permissioncontroller"));

    /** Words in the dialog, in order (the camera's "record video" before photos and videos). */
    private static final String[][] GROUP_WORDS = {
            {"location", "location"},
            {"camera", "take pictures"},
            {"microphone", "record audio"},
            {"nearby", "nearby devices"},
            {"contacts", "contacts"},
            {"sms", "sms"},
            {"sms", "text messages"},
            {"phone", "phone calls"},
            {"calendar", "calendar"},
            {"photos", "photos and videos"},
            {"photos", "music and audio"},
            {"photos", "photos, media"},
            {"photos", "files"},
    };

    private PermissionGate() {}

    /** Android's grant dialog ("Allow X to access ...?"). */
    public static boolean isGrantDialog(String pkg, String cls) {
        if (pkg == null || !CONTROLLERS.contains(pkg)) return false;
        String lower = cls == null ? "" : cls.toLowerCase(Locale.ROOT);
        return lower.contains("grantpermissions");
    }

    /** The kind of permission a grant dialog asks for, or null when it is not one we know. */
    public static String group(String text) {
        String lower = text == null ? "" : text.toLowerCase(Locale.ROOT);
        for (String[] pair : GROUP_WORDS) {
            if (lower.contains(pair[1])) return pair[0];
        }
        return null;
    }

    /**
     * Whether the dialog must be dismissed: the kind is gated by a parent and
     * this app has no approval for it. Jarvis's own prompts are never held.
     */
    public static boolean holds(Collection<String> gated, Map<String, String> approvals, String app, String group) {
        if (group == null || gated == null || !gated.contains(group)) return false;
        if (app == null || app.isEmpty() || GuardRules.OWN_PACKAGE.equals(app)) return false;
        return approvals == null || !InstallApprovals.APPROVED.equals(approvals.get(target(app, group)));
    }

    /** The request target the server keeps: "package|group". */
    public static String target(String app, String group) {
        return app + "|" + group;
    }
}
