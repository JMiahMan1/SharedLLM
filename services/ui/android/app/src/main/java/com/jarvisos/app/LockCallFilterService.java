package com.jarvisos.app;

import android.app.role.RoleManager;
import android.content.Context;
import android.net.Uri;
import android.telecom.CallRedirectionService;
import android.telecom.PhoneAccountHandle;
import android.telephony.TelephonyManager;

/**
 * The lock's call rule ({@link LockCommsRules#callAllowed}): while a child's
 * phone is locked, an outgoing call goes through only to a parent's number.
 *
 * Android's call redirection role (ROLE_CALL_REDIRECTION) hands this service
 * every outgoing call Telecom places, whether it was dialled on the phone or
 * from a paired watch or car over Bluetooth, before it is placed. Emergency
 * calls are never handed to a call redirection service, so 911 always goes
 * through. Outside a lock every call is placed unmodified.
 *
 * The role needs a one-time consent on the phone (the guard setup step); until
 * it is held, a device owner takes outgoing calls away for the lock instead
 * ({@link LockCommsRules#blockAllOutgoing}).
 */
public class LockCallFilterService extends CallRedirectionService {

    @Override
    public void onPlaceCall(Uri handle, PhoneAccountHandle initialPhoneAccount, boolean allowInteractiveResponse) {
        boolean allowed;
        try {
            Context context = getApplicationContext();
            String number = handle == null ? null : handle.getSchemeSpecificPart();
            boolean active = LockCommsRules.active(GuardApi.isProtected(context),
                    GuardApi.isLocked(context), GuardApi.isUnlocked(context));
            allowed = LockCommsRules.callAllowed(active, number, GuardApi.lockCallAllow(context),
                    isEmergency(context, number));
            if (!allowed) {
                GuardNotices.warn(context, "Calls are paused",
                        "While your phone is locked you can call your parents or 911.");
            }
        } catch (Exception e) {
            // A filter that cannot read its own state must not strand a call
            // outside a lock; inside one the device-owner fallback still holds.
            allowed = !LockCommsRules.active(GuardApi.isProtected(this),
                    GuardApi.isLocked(this), GuardApi.isUnlocked(this));
        }
        // The outcome only, never the number.
        android.util.Log.i(DeviceOwnerGuard.TAG, allowed ? "call filter: placed" : "call filter: cancelled while locked");
        if (allowed) {
            placeCallUnmodified();
        } else {
            cancelCall();
        }
    }

    private static boolean isEmergency(Context context, String number) {
        if (number == null || number.isEmpty()) return false;
        try {
            TelephonyManager tm = (TelephonyManager) context.getSystemService(Context.TELEPHONY_SERVICE);
            return tm != null && tm.isEmergencyNumber(number);
        } catch (Exception e) {
            return false;
        }
    }

    /** Whether Jarvis holds the call redirection role (the lock's call filter can run). */
    static boolean held(Context context) {
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
        try {
            RoleManager rm = context.getSystemService(RoleManager.class);
            return rm != null && rm.isRoleAvailable(RoleManager.ROLE_CALL_REDIRECTION);
        } catch (Exception e) {
            return false;
        }
    }
}
