package com.jarvisos.app;

import java.util.Arrays;
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

    private static final Set<String> RESTRICTIONS = Collections.unmodifiableSet(
            new LinkedHashSet<>(Arrays.asList(DISALLOW_SAFE_BOOT, DISALLOW_FACTORY_RESET,
                    DISALLOW_USER_SWITCH, DISALLOW_ADD_USER, DISALLOW_DEBUGGING_FEATURES,
                    DISALLOW_CONFIG_VPN, DISALLOW_CONFIG_PRIVATE_DNS)));

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
     * Whether a screen-time lock is a real OS lock now: a device owner, on a
     * protected phone, locked, and not inside a parent's unlock window. Without
     * device owner the guard falls back to the Accessibility soft lock.
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
     * device owner the guard falls back to the Accessibility soft lock, and
     * this is always false.
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
