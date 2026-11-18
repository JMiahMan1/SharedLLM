package com.jarvisos.app;

import android.app.role.RoleManager;
import android.content.Context;
import android.os.Build;

/**
 * The call redirection role behind {@link LockCallFilterService}. Kept apart
 * from the service because the service extends an Android 10 class: calling a
 * static method on it would load it, which fails on Android 9.
 */
final class CallFilterRole {

    private CallFilterRole() {}

    /** Whether Jarvis holds the call redirection role (the lock's call filter can run). */
    static boolean held(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false;
        try {
            RoleManager rm = context.getSystemService(RoleManager.class);
            return rm != null && rm.isRoleAvailable(RoleManager.ROLE_CALL_REDIRECTION)
                    && rm.isRoleHeld(RoleManager.ROLE_CALL_REDIRECTION);
        } catch (Exception e) {
            return false;
        }
    }

    /** Whether this phone offers the call redirection role at all. */
    static boolean available(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false;
        try {
            RoleManager rm = context.getSystemService(RoleManager.class);
            return rm != null && rm.isRoleAvailable(RoleManager.ROLE_CALL_REDIRECTION);
        } catch (Exception e) {
            return false;
        }
    }
}
