package com.jarvisos.app;

import java.util.Arrays;
import java.util.Collection;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * What the screen guard does with the screen that just opened: let it be,
 * cover it because it would remove or switch off Jarvis's protection, or cover
 * it with the full-screen lock overlay (the guard's own screen, on top of
 * everything) because screen time is up (bedtime, school, the limit, a
 * parent's pause), or close one app a parent switched off or whose own
 * minutes are used up (app control).
 *
 * Plain Java with no Android types, so every case is unit-tested on the JVM;
 * GuardService feeds it the window's package, class and visible text.
 */
public final class GuardRules {

    public enum Action { ALLOW, BLOCK_TAMPER, BLOCK_LOCKED, BLOCK_APP, BLOCK_INSTALL, BLOCK_BYPASS }

    /**
     * How a blocked window is taken off the screen: going home closes a
     * full-screen app; a split screen is ended first (home alone can leave the
     * other half showing); a picture-in-picture window is dismissed, since it
     * floats over home.
     */
    public enum Clear { HOME, EXIT_SPLIT, DISMISS_PIP }

    /** The app's own name as Settings and the uninstaller show it. */
    static final String APP_LABEL = "jarvis os";
    /**
     * The Accessibility service's label, as its detail page (and the services
     * list) shows it; distinct from {@link #APP_LABEL}, which names the app.
     */
    static final String GUARD_SERVICE_LABEL = "jarvis screen guard";
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

    /**
     * Never covered by a screen-time lock: the guard's own screen, the system UI,
     * emergency calls and the incoming-call screens (so a call can always be
     * answered, even before the server's always-allowed list has arrived).
     */
    static final Set<String> NEVER_LOCKED = new HashSet<>(Arrays.asList(
            OWN_PACKAGE,
            "com.android.systemui",
            "com.android.emergency",
            "com.android.phone",
            "com.android.incallui",
            "com.samsung.android.incallui",
            "com.android.server.telecom"));

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
        return decide(protectedPhone, locked, unlocked, pkg, cls, text, alwaysAllowed, launcher, blockedApps, null);
    }

    /**
     * As above, with {@code awaitingApproval}: apps installed since the guard
     * started that a parent has not approved (InstallApprovals). They stay
     * closed, after the locks and blocks above, until a parent says yes.
     */
    public static Action decide(boolean protectedPhone, boolean locked, boolean unlocked,
                                String pkg, String cls, String text,
                                Collection<String> alwaysAllowed, String launcher,
                                Collection<String> blockedApps, Collection<String> awaitingApproval) {
        return decide(protectedPhone, locked, unlocked, pkg, cls, text, alwaysAllowed, launcher,
                blockedApps, awaitingApproval, false, null);
    }

    /**
     * As above, with the web filter: while {@code filterOn} (the parents want
     * it), Settings' Private DNS dialog is covered like a tamper screen and
     * {@code bypassApps} (FilterBypass: apps that get around a DNS filter) are
     * closed, after the locks and parents' blocks.
     */
    public static Action decide(boolean protectedPhone, boolean locked, boolean unlocked,
                                String pkg, String cls, String text,
                                Collection<String> alwaysAllowed, String launcher,
                                Collection<String> blockedApps, Collection<String> awaitingApproval,
                                boolean filterOn, Collection<String> bypassApps) {
        if (pkg == null || pkg.isEmpty() || unlocked) return Action.ALLOW;
        String lowerText = text == null ? "" : text.toLowerCase(Locale.ROOT);
        String lowerCls = cls == null ? "" : cls.toLowerCase(Locale.ROOT);

        if (protectedPhone && isTamperScreen(pkg, lowerCls, lowerText)) return Action.BLOCK_TAMPER;
        if (protectedPhone && filterOn && FilterBypass.isPrivateDnsScreen(pkg, lowerText)) return Action.BLOCK_TAMPER;

        if (exempt(pkg, launcher, alwaysAllowed)) return Action.ALLOW;
        if (locked) return Action.BLOCK_LOCKED;
        if (blockedApps != null && blockedApps.contains(pkg)) return Action.BLOCK_APP;
        if (protectedPhone && filterOn && bypassApps != null && bypassApps.contains(pkg)) return Action.BLOCK_BYPASS;
        if (protectedPhone && awaitingApproval != null && awaitingApproval.contains(pkg)) return Action.BLOCK_INSTALL;
        return Action.ALLOW;
    }

    /**
     * How to take a blocked window off the screen.
     *
     * @param pip        the window is picture-in-picture
     * @param appWindows how many app windows are on screen (two or more: split screen)
     */
    public static Clear clearFor(boolean pip, int appWindows) {
        if (pip) return Clear.DISMISS_PIP;
        return appWindows > 1 ? Clear.EXIT_SPLIT : Clear.HOME;
    }

    /**
     * Phone starts since the guard last ran in which it never ran: a start in
     * safe mode (where Android runs no downloaded app), or one switched off
     * before the guard came up. Counted from Android's boot counter
     * ({@code Settings.Global.BOOT_COUNT}); 0 when either reading is missing
     * or the counter went backwards (a reset phone).
     */
    public static int bootsWithoutGuard(int lastSeenBoot, int currentBoot) {
        if (lastSeenBoot <= 0 || currentBoot <= 0 || currentBoot <= lastSeenBoot) return 0;
        return currentBoot - lastSeenBoot - 1;
    }

    /** Never closed by a lock or block: the guard and system screens, home, the always-allowed apps. */
    static boolean exempt(String pkg, String launcher, Collection<String> alwaysAllowed) {
        return NEVER_LOCKED.contains(pkg)
                || pkg.equals(launcher)
                || (alwaysAllowed != null && alwaysAllowed.contains(pkg));
    }

    /**
     * Never covered by the full-screen lock overlay: the guard's own screens and
     * the system UI (so a parent's login and an emergency call still open), and
     * the always-allowed apps (calls, texts, Jarvis). Unlike {@link #exempt} the
     * home screen is deliberately NOT here, so Home cannot dismiss the lock.
     */
    public static boolean lockOverlayExempt(String pkg, Collection<String> alwaysAllowed) {
        if (pkg == null || pkg.isEmpty()) return false;
        return NEVER_LOCKED.contains(pkg) || (alwaysAllowed != null && alwaysAllowed.contains(pkg));
    }

    /**
     * Whether the full-screen lock overlay should cover the screen now: on a
     * protected phone that is locked, outside a parent's unlock window, and not
     * a device owner (a device owner gets Android's own lock screen and lock-task
     * mode instead), with a window in front that the lock does not leave alone.
     *
     * The overlay is what makes a lock on a phone that is not a device owner
     * cover the whole screen - the home screen included - and stay put when the
     * child presses Home or Back, instead of only sending the child home.
     */
    public static boolean lockOverlay(boolean deviceOwner, boolean protectedPhone, boolean locked,
                                      boolean unlocked, String pkg, Collection<String> alwaysAllowed) {
        if (deviceOwner || !protectedPhone || !locked || unlocked) return false;
        return !lockOverlayExempt(pkg, alwaysAllowed);
    }

    /** Android's VPN dialogs; the one that manages a running VPN can disconnect the web filter. */
    static final String VPN_DIALOGS = "com.android.vpndialogs";

    /**
     * Settings text on a page that would get around the guard: resetting or
     * erasing the phone, granting usage access, or handing the phone to another
     * user (Users / Add user), where Jarvis is not installed. Matched only on
     * Settings pages, so the same words elsewhere (a web page, an app) are left
     * alone. "usage access" and not "usage" so ordinary pages such as Data usage
     * are not covered.
     */
    static final Set<String> TAMPER_TEXTS = new HashSet<>(Arrays.asList(
            "factory reset", "erase all data", "erase everything",
            "usage access", "add user", "user & account", "users"));

    /** A screen that would uninstall Jarvis or switch its protection off. */
    static boolean isTamperScreen(String pkg, String lowerCls, String lowerText) {
        boolean namesJarvis = lowerText.contains(APP_LABEL);
        if (VPN_DIALOGS.equals(pkg)) {
            // "Disconnect" on the web filter; the first-time consent dialog stays open.
            return lowerCls.contains("manage") || lowerText.contains("disconnect");
        }
        if (INSTALLERS.contains(pkg)) {
            // The uninstall dialog ("Do you want to uninstall Jarvis OS?").
            return namesJarvis || lowerCls.contains("uninstall");
        }
        if (SETTINGS.contains(pkg)) {
            // Device admin apps, and any page about Jarvis itself (app info,
            // its accessibility switch, force stop, clear storage). The guard's
            // own Accessibility detail page names its service label, not the app.
            // The factory reset, usage access and Users pages are ways around it.
            return lowerCls.contains("deviceadmin") || namesJarvis
                    || lowerText.contains(GUARD_SERVICE_LABEL)
                    || containsAny(lowerText, TAMPER_TEXTS);
        }
        return false;
    }

    /** Whether any of {@code needles} appears in {@code text}. */
    private static boolean containsAny(String text, Collection<String> needles) {
        for (String needle : needles) {
            if (text.contains(needle)) return true;
        }
        return false;
    }
}
