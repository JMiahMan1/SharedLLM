package com.jarvisos.app;

import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * Layer 2 (device owner): what Jarvis's guard can enforce through Android
 * itself when the phone was provisioned as a device owner (see
 * docs/HEALTH_HUB.md), instead of only what the Accessibility service can do.
 *
 * Plain Java with no Android types, so every case is unit-tested on the JVM;
 * {@code DeviceOwnerGuard} makes the calls and {@code GuardService} feeds it the
 * state (device owner, protected, locked, a parent's unlock window).
 *
 * While the phone is the device owner and belongs to a minor ({@code protected}):
 *   - the uninstall block makes removing Jarvis impossible (Android refuses it),
 *   - DISALLOW_SAFE_BOOT closes safe mode as a way around the guard,
 *   - DISALLOW_FACTORY_RESET closes the factory reset as a way around it,
 *   - DISALLOW_USER_SWITCH and DISALLOW_ADD_USER close the second user or guest
 *     that would run without Jarvis and escape the guard,
 *   - DISALLOW_DEBUGGING_FEATURES closes adb / USB debugging as a way in,
 *   - DISALLOW_CONFIG_VPN and DISALLOW_CONFIG_PRIVATE_DNS keep the web filter
 *     from being switched off from Settings,
 *   - DISALLOW_INSTALL_UNKNOWN_SOURCES and DISALLOW_INSTALL_UNKNOWN_SOURCES_GLOBALLY
 *     keep APK sideloading off,
 *   - DISALLOW_CONFIG_DATE_TIME prevents shifting the clock or timezone to
 *     escape bedtime, school hours or daily budgets,
 *   - DISALLOW_APPS_CONTROL prevents force-stopping or clearing app storage in
 *     Settings,
 *   - DISALLOW_MOUNT_PHYSICAL_MEDIA prevents mounting external USB/SD storage,
 *   - always-on VPN keeps WebFilterService connected across reboots when the
 *     parents want the web filter on,
 *   - OS-level package suspension greys out and blocks launching apps in
 *     blocked_apps or awaiting install approval,
 *   - remote uninstall removes packages in uninstall_apps silently,
 *   - COSU kiosk mode pins the lock-task allowlist to Jarvis, always_allowed
 *     and kiosk_apps,
 *   - a screen-time lock is a real OS lock (lockNow plus lock-task / kiosk
 *     mode, which pins the phone to the guard screen), not only going home.
 * A parent's unlock window lifts the lock (they can use the phone); the uninstall
 * block and the restrictions stay, since removing them needs more than the
 * ten-minute window. When the server says this is no longer a minor's phone the
 * restrictions and the uninstall block are taken off again.
 */
public final class DeviceOwnerRules {

    /** Android's UserManager.DISALLOW_SAFE_BOOT value: safe mode cannot start without the guard. */
    public static final String DISALLOW_SAFE_BOOT = "no_safe_boot";
    /** Android's UserManager.DISALLOW_FACTORY_RESET value: the factory reset is refused. */
    public static final String DISALLOW_FACTORY_RESET = "no_factory_reset";
    /** Android's UserManager.DISALLOW_USER_SWITCH value: no switching to another user who has no guard. */
    public static final String DISALLOW_USER_SWITCH = "no_user_switch";
    /** Android's UserManager.DISALLOW_ADD_USER value: no second user or guest to escape into. */
    public static final String DISALLOW_ADD_USER = "no_add_user";
    /** Android's UserManager.DISALLOW_DEBUGGING_FEATURES value: no USB debugging / adb way in. */
    public static final String DISALLOW_DEBUGGING_FEATURES = "no_debugging_features";
    /** Android's UserManager.DISALLOW_CONFIG_VPN value: the web filter's VPN cannot be switched off. */
    public static final String DISALLOW_CONFIG_VPN = "no_config_vpn";
    /** Android's UserManager.DISALLOW_CONFIG_PRIVATE_DNS value: Private DNS cannot be moved off the filter. */
    public static final String DISALLOW_CONFIG_PRIVATE_DNS = "no_config_private_dns";
    /** Android's UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES value: no sideloading APKs. */
    public static final String DISALLOW_INSTALL_UNKNOWN_SOURCES = "no_install_unknown_sources";
    /** Android's UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES_GLOBALLY value: device-wide sideload block. */
    public static final String DISALLOW_INSTALL_UNKNOWN_SOURCES_GLOBALLY = "no_install_unknown_sources_globally";
    /** Android's UserManager.DISALLOW_CONFIG_DATE_TIME value: clock/timezone cannot be shifted around budgets. */
    public static final String DISALLOW_CONFIG_DATE_TIME = "no_config_date_time";
    /** Android's UserManager.DISALLOW_APPS_CONTROL value: no force-stopping or clearing app storage in Settings. */
    public static final String DISALLOW_APPS_CONTROL = "no_control_apps";
    /** Android's UserManager.DISALLOW_MOUNT_PHYSICAL_MEDIA value: no mounting external USB/SD media. */
    public static final String DISALLOW_MOUNT_PHYSICAL_MEDIA = "no_physical_media";

    private static final Set<String> RESTRICTIONS = Collections.unmodifiableSet(
            new LinkedHashSet<>(Arrays.asList(DISALLOW_SAFE_BOOT, DISALLOW_FACTORY_RESET,
                    DISALLOW_USER_SWITCH, DISALLOW_ADD_USER, DISALLOW_DEBUGGING_FEATURES,
                    DISALLOW_CONFIG_VPN, DISALLOW_CONFIG_PRIVATE_DNS,
                    DISALLOW_INSTALL_UNKNOWN_SOURCES, DISALLOW_INSTALL_UNKNOWN_SOURCES_GLOBALLY,
                    DISALLOW_CONFIG_DATE_TIME, DISALLOW_APPS_CONTROL, DISALLOW_MOUNT_PHYSICAL_MEDIA)));

    private DeviceOwnerRules() {}

    /** The user restrictions a device owner adds on a protected phone (empty when it is not one). */
    public static Set<String> restrictions(boolean deviceOwner, boolean protectedPhone) {
        return deviceOwner && protectedPhone ? RESTRICTIONS : Collections.emptySet();
    }

    /**
     * The restrictions to take off, when the server has said (not merely not
     * answered) that this is no longer a minor's phone. With no status yet they
     * stay, so a phone that is offline for a while keeps its protection.
     */
    public static Set<String> restrictionsToClear(boolean deviceOwner, boolean protectedPhone, boolean statusKnown) {
        return deviceOwner && statusKnown && !protectedPhone ? RESTRICTIONS : Collections.emptySet();
    }

    /** Whether to make Jarvis impossible to uninstall: a device owner on a protected phone. */
    public static boolean blockUninstall(boolean deviceOwner, boolean protectedPhone) {
        return deviceOwner && protectedPhone;
    }

    /** Whether to lift the uninstall block: the server says this is no longer a minor's phone. */
    public static boolean unblockUninstall(boolean deviceOwner, boolean protectedPhone, boolean statusKnown) {
        return deviceOwner && statusKnown && !protectedPhone;
    }

    /**
     * Package name to configure as Android's always-on VPN on a device-owner
     * protected phone when the web filter is wanted; null otherwise.
     */
    public static String alwaysOnVpnPackage(boolean deviceOwner, boolean protectedPhone,
                                            boolean webFilterWanted, String jarvisPackage) {
        if (!deviceOwner || !protectedPhone || !webFilterWanted) return null;
        if (jarvisPackage == null || jarvisPackage.isEmpty()) return null;
        return jarvisPackage;
    }

    /**
     * Whether to clear the always-on VPN package: when the server has spoken and
     * either the phone is no longer a minor's or the web filter was switched off.
     */
    public static boolean clearAlwaysOnVpn(boolean deviceOwner, boolean protectedPhone,
                                           boolean webFilterWanted, boolean statusKnown) {
        return deviceOwner && statusKnown && (!protectedPhone || !webFilterWanted);
    }

    /**
     * Packages to suspend at the OS level ({@code setPackagesSuspended(..., true)})
     * on a device-owner protected phone outside a parent's unlock window. Never
     * suspends Jarvis itself, {@link GuardRules#NEVER_LOCKED} or {@code alwaysAllowed}.
     */
    public static Set<String> packagesToSuspend(boolean deviceOwner, boolean protectedPhone, boolean unlocked,
                                                Collection<String> blockedApps, Collection<String> awaitingApproval,
                                                Collection<String> alwaysAllowed, String jarvisPackage) {
        if (!deviceOwner || !protectedPhone || unlocked) return Collections.emptySet();
        Set<String> out = new LinkedHashSet<>();
        addSuspendable(out, blockedApps, alwaysAllowed, jarvisPackage);
        addSuspendable(out, awaitingApproval, alwaysAllowed, jarvisPackage);
        return out;
    }

    private static void addSuspendable(Set<String> out, Collection<String> candidates,
                                       Collection<String> alwaysAllowed, String jarvisPackage) {
        if (candidates == null) return;
        for (String pkg : candidates) {
            if (pkg == null || pkg.isEmpty()) continue;
            if (pkg.equals(jarvisPackage) || GuardRules.NEVER_LOCKED.contains(pkg)) continue;
            if (alwaysAllowed != null && alwaysAllowed.contains(pkg)) continue;
            out.add(pkg);
        }
    }

    /**
     * Packages previously suspended by Jarvis that should now be unsuspended
     * because a parent unblocked or approved them, opened an unlock window, or
     * the server said this is no longer a minor's phone.
     */
    public static Set<String> packagesToUnsuspend(boolean deviceOwner, boolean statusKnown,
                                                  Collection<String> previouslySuspended,
                                                  Collection<String> nowSuspended) {
        if (!deviceOwner || !statusKnown || previouslySuspended == null || previouslySuspended.isEmpty()) {
            return Collections.emptySet();
        }
        Set<String> out = new LinkedHashSet<>();
        for (String pkg : previouslySuspended) {
            if (pkg == null || pkg.isEmpty()) continue;
            if (nowSuspended == null || !nowSuspended.contains(pkg)) {
                out.add(pkg);
            }
        }
        return out;
    }

    /**
     * Packages a parent queued for remote uninstall ({@code uninstall_apps})
     * that are currently installed on this device-owner protected phone. Never
     * uninstalls Jarvis itself, {@link GuardRules#NEVER_LOCKED} or {@code alwaysAllowed}.
     */
    public static Set<String> packagesToUninstall(boolean deviceOwner, boolean protectedPhone,
                                                  Collection<String> uninstallApps,
                                                  Collection<String> installedApps,
                                                  Collection<String> alwaysAllowed,
                                                  String jarvisPackage) {
        if (!deviceOwner || !protectedPhone || uninstallApps == null || installedApps == null) {
            return Collections.emptySet();
        }
        Set<String> out = new LinkedHashSet<>();
        for (String pkg : uninstallApps) {
            if (pkg == null || pkg.isEmpty()) continue;
            if (pkg.equals(jarvisPackage) || GuardRules.NEVER_LOCKED.contains(pkg)) continue;
            if (alwaysAllowed != null && alwaysAllowed.contains(pkg)) continue;
            if (installedApps.contains(pkg)) out.add(pkg);
        }
        return out;
    }

    /**
     * Packages a parent queued for remote install ({@code install_apps}) that
     * are not yet installed on this protected phone.
     */
    public static Set<String> packagesToInstall(boolean protectedPhone,
                                                Collection<String> installApps,
                                                Collection<String> installedApps) {
        if (!protectedPhone || installApps == null) return Collections.emptySet();
        Set<String> out = new LinkedHashSet<>();
        for (String pkg : installApps) {
            if (pkg == null || pkg.isEmpty()) continue;
            if (installedApps == null || !installedApps.contains(pkg)) {
                out.add(pkg);
            }
        }
        return out;
    }

    /**
     * Whether COSU dedicated kiosk mode is active on a device-owner phone right
     * now (protected, not unlocked, not screen-time locked, and kioskMode on).
     */
    public static boolean kioskActive(boolean deviceOwner, boolean protectedPhone,
                                      boolean locked, boolean unlocked, boolean kioskMode) {
        return deviceOwner && protectedPhone && !locked && !unlocked && kioskMode;
    }

    /**
     * Lock-task allowlist for COSU kiosk mode on a device-owner phone: Jarvis,
     * {@link GuardRules#NEVER_LOCKED}, {@code alwaysAllowed} and {@code kioskApps}.
     */
    public static Set<String> kioskPackages(boolean deviceOwner, boolean protectedPhone,
                                            boolean locked, boolean unlocked, boolean kioskMode,
                                            Collection<String> kioskApps,
                                            Collection<String> alwaysAllowed,
                                            String jarvisPackage) {
        if (!kioskActive(deviceOwner, protectedPhone, locked, unlocked, kioskMode)) {
            return Collections.emptySet();
        }
        Set<String> out = new LinkedHashSet<>();
        if (jarvisPackage != null && !jarvisPackage.isEmpty()) out.add(jarvisPackage);
        out.addAll(GuardRules.NEVER_LOCKED);
        if (alwaysAllowed != null) {
            for (String pkg : alwaysAllowed) {
                if (pkg != null && !pkg.isEmpty()) out.add(pkg);
            }
        }
        if (kioskApps != null) {
            for (String pkg : kioskApps) {
                if (pkg != null && !pkg.isEmpty()) out.add(pkg);
            }
        }
        return out;
    }

    /**
     * Whether a screen-time lock is a real OS lock now: a device owner, on a
     * protected phone, locked, and not inside a parent's unlock window. Without
     * device owner the guard covers the screen with the Accessibility overlay
     * instead ({@link GuardOverlay}), which this is not about.
     */
    public static boolean hardLock(boolean deviceOwner, boolean protectedPhone, boolean locked, boolean unlocked) {
        return deviceOwner && protectedPhone && locked && !unlocked;
    }

    /**
     * Whether the phone should be pinned to the guard's screen (Android's
     * lock-task / kiosk mode) now: a device owner, on a protected phone, locked,
     * and not inside a parent's unlock window. This is the stronger of the two
     * device-owner locks: {@link #hardLock} puts Android's lock screen up once,
     * while lock-task mode keeps the child on the guard screen, so the launcher
     * and every other app stay out of reach until a parent unlocks. Without
     * device owner the guard covers the screen with the Accessibility overlay
     * instead ({@link GuardOverlay}), and this is always false.
     */
    public static boolean lockTask(boolean deviceOwner, boolean protectedPhone, boolean locked, boolean unlocked) {
        return deviceOwner && protectedPhone && locked && !unlocked;
    }

    /**
     * Whether to erase the phone now: a parent queued a remote wipe and this is
     * a device owner. Only a device owner may call
     * {@code DevicePolicyManager.wipeData}, so without one the request is a no-op
     * rather than a silent failure that looks like success. Never true unless a
     * wipe was actually requested.
     */
    public static boolean shouldWipe(boolean deviceOwner, boolean wipeRequested) {
        return deviceOwner && wipeRequested;
    }
}
