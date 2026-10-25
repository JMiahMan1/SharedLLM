package com.jarvisos.app;

import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;
import android.content.Context;

/**
 * Layer 2 (device owner): the Android calls behind {@link DeviceOwnerRules},
 * made on a phone provisioned as a device owner (docs/HEALTH_HUB.md). Every
 * decision is in the pure, JVM-tested {@code DeviceOwnerRules}; this class only
 * asks DevicePolicyManager to apply it.
 *
 * Without device owner every method here does nothing: the Accessibility guard
 * (Layer 1) is then the whole of the protection, as before.
 */
final class DeviceOwnerGuard {

    private DeviceOwnerGuard() {}

    /** Whether this app is the phone's device owner. */
    static boolean isOwner(Context context) {
        DevicePolicyManager dpm = dpm(context);
        return dpm != null && dpm.isDeviceOwnerApp(context.getPackageName());
    }

    /**
     * Re-applies the device-owner enforcement: the uninstall block while the
     * phone is a protected child's, and the safe-boot / factory-reset
     * restrictions (or takes them off once the server says this is no longer a
     * minor's phone). Android enforces these itself; the guard only sets them.
     * Idempotent, so it runs on every status refresh.
     */
    static void enforce(Context context) {
        DevicePolicyManager dpm = dpm(context);
        boolean owner = dpm != null && dpm.isDeviceOwnerApp(context.getPackageName());
        if (!owner) return;
        boolean prot = GuardApi.isProtected(context);
        boolean known = GuardApi.statusKnown(context);
        ComponentName admin = new ComponentName(context, GuardAdminReceiver.class);
        // The values in DeviceOwnerRules mirror Android's UserManager.DISALLOW_* constants.
        for (String key : DeviceOwnerRules.restrictions(owner, prot)) {
            try {
                dpm.addUserRestriction(admin, key);
            } catch (Exception ignored) {
                // An older Android without this restriction: the others still apply.
            }
        }
        for (String key : DeviceOwnerRules.restrictionsToClear(owner, prot, known)) {
            try {
                dpm.clearUserRestriction(admin, key);
            } catch (Exception ignored) {
                // Not set: nothing to take off.
            }
        }
        if (DeviceOwnerRules.blockUninstall(owner, prot)) {
            try {
                dpm.setUninstallBlocked(admin, context.getPackageName(), true);
            } catch (Exception ignored) {
                // Not device owner any more: nothing to block.
            }
        } else if (DeviceOwnerRules.unblockUninstall(owner, prot, known)) {
            try {
                dpm.setUninstallBlocked(admin, context.getPackageName(), false);
            } catch (Exception ignored) {
                // Nothing to lift.
            }
        }
    }

    /**
     * A real OS lock (Android's own lock screen), for a screen-time lock on a
     * device owner. Call only when {@link DeviceOwnerRules#hardLock} says so;
     * without device owner the guard falls back to the Accessibility soft lock.
     */
    static void lockNow(Context context) {
        DevicePolicyManager dpm = dpm(context);
        if (dpm == null) return;
        try {
            dpm.lockNow();
        } catch (Exception ignored) {
            // No secure lock screen, or no longer device owner: the soft lock still runs.
        }
    }

    /**
     * Erase the phone (a factory reset) at a parent's explicit request. Only a
     * device owner may; call only when {@link DeviceOwnerRules#shouldWipe} says
     * so. This cannot be undone: the phone comes back to its out-of-box state.
     */
    static void wipeData(Context context) {
        DevicePolicyManager dpm = dpm(context);
        if (dpm == null) return;
        try {
            dpm.wipeData(0);
        } catch (Exception ignored) {
            // No longer device owner, or the platform refused: nothing else to do.
        }
    }

    private static DevicePolicyManager dpm(Context context) {
        return (DevicePolicyManager) context.getSystemService(Context.DEVICE_POLICY_SERVICE);
    }
}
