package com.jarvisos.app;

import java.util.Arrays;
import java.util.Collection;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * What the screen guard does with the screen that just opened: let it be,
 * cover it because it would remove or switch off Jarvis's protection, or send
 * the child home because screen time is up (bedtime, school, the limit, a
 * parent's pause), or close one app a parent switched off or whose own
 * minutes are used up (app control).
 *
 * Plain Java with no Android types, so every case is unit-tested on the JVM;
 * GuardService feeds it the window's package, class and visible text.
 */
public final class GuardRules {

    public enum Action { ALLOW, BLOCK_TAMPER, BLOCK_LOCKED, BLOCK_APP }

    /** The app's own name as Settings and the uninstaller show it. */
    static final String APP_LABEL = "jarvis os";
    static final String OWN_PACKAGE = "com.jarvisos.app";

    /** The package installer: its uninstall dialog names the app. */
    static final Set<String> INSTALLERS = new HashSet<>(Arrays.asList(
            "com.google.android.packageinstaller",
            "com.android.packageinstaller",
            "com.samsung.android.packageinstaller"));

    /** Settings apps where Jarvis's app info, device admin and accessibility pages live. */
    static final Set<String> SETTINGS = new HashSet<>(Arrays.asList(
            "com.android.settings",
            "com.samsung.android.settings",
            "com.miui.securitycenter",
            "com.google.android.permissioncontroller"));

    /** Never covered by a screen-time lock: the guard's own screen, the system UI and emergency calls. */
    static final Set<String> NEVER_LOCKED = new HashSet<>(Arrays.asList(
            OWN_PACKAGE,
            "com.android.systemui",
            "com.android.emergency",
            "com.android.phone"));

    private GuardRules() {}

    /**
     * @param protectedPhone the phone belongs to a minor (Jarvis must not be removable)
     * @param locked         the server's screen-time status says locked now
     * @param unlocked       a parent signed in on this phone within the unlock window
     * @param pkg            the package of the window that opened
     * @param cls            its class name (may be empty)
     * @param text           the window's visible text, joined (may be empty)
     * @param alwaysAllowed  packages the screen-time lock never covers
     * @param launcher       the phone's home-screen package (going home is always allowed)
     * @param blockedApps    apps blocked now: switched off by a parent, or past their own minutes
     */
    public static Action decide(boolean protectedPhone, boolean locked, boolean unlocked,
                                String pkg, String cls, String text,
                                Collection<String> alwaysAllowed, String launcher,
                                Collection<String> blockedApps) {
        if (pkg == null || pkg.isEmpty() || unlocked) return Action.ALLOW;
        String lowerText = text == null ? "" : text.toLowerCase(Locale.ROOT);
        String lowerCls = cls == null ? "" : cls.toLowerCase(Locale.ROOT);

        if (protectedPhone && isTamperScreen(pkg, lowerCls, lowerText)) return Action.BLOCK_TAMPER;

        if (locked
                && !NEVER_LOCKED.contains(pkg)
                && !pkg.equals(launcher)
                && (alwaysAllowed == null || !alwaysAllowed.contains(pkg))) {
            return Action.BLOCK_LOCKED;
        }
        if (blockedApps != null
                && blockedApps.contains(pkg)
                && !NEVER_LOCKED.contains(pkg)
                && !pkg.equals(launcher)
                && (alwaysAllowed == null || !alwaysAllowed.contains(pkg))) {
            return Action.BLOCK_APP;
        }
        return Action.ALLOW;
    }

    /** A screen that would uninstall Jarvis or switch its protection off. */
    static boolean isTamperScreen(String pkg, String lowerCls, String lowerText) {
        boolean namesJarvis = lowerText.contains(APP_LABEL);
        if (INSTALLERS.contains(pkg)) {
            // The uninstall dialog ("Do you want to uninstall Jarvis OS?").
            return namesJarvis || lowerCls.contains("uninstall");
        }
        if (SETTINGS.contains(pkg)) {
            // Device admin apps, and any page about Jarvis itself (app info,
            // its accessibility switch, force stop, clear storage).
            return lowerCls.contains("deviceadmin") || namesJarvis;
        }
        return false;
    }
}
