package com.jarvisos.app;

import android.app.admin.DeviceAdminReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Device admin for a child's phone: while it is active, Jarvis cannot be
 * uninstalled. Switching it off is a Settings page the screen guard covers
 * with a parent-login screen; if it is switched off anyway, the parents hear.
 *
 * A device owner (set up once over USB, see docs/HEALTH_HUB.md) goes further
 * and blocks the uninstall outright.
 */
public class GuardAdminReceiver extends DeviceAdminReceiver {

    @Override
    public CharSequence onDisableRequested(Context context, Intent intent) {
        return "Turning this off needs a parent. Your parents will be told.";
    }

    @Override
    public void onDisabled(Context context, Intent intent) {
        if (GuardApi.isProtected(context) && !GuardApi.isUnlocked(context)) {
            GuardApi.reportTamper(context, "admin_disabled");
        }
    }
}
