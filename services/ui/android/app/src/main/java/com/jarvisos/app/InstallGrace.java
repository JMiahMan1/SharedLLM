package com.jarvisos.app;

import android.content.Context;

/**
 * Ten-minute grace window after Jarvis starts an approved install (or a
 * self-update), during which installer screens and Settings pages that only
 * name Jarvis as an install source are not reported as tamper.
 */
final class InstallGrace {
    static final long WINDOW_MS = 10 * 60_000L;
    private static final String KEY_UNTIL = "guard_install_grace_until";

    private InstallGrace() {}

    static void record(Context context) {
        if (context == null) return;
        GuardApi.prefs(context).edit().putLong(KEY_UNTIL, System.currentTimeMillis() + WINDOW_MS).apply();
    }

    static boolean inProgress(Context context) {
        if (context == null) return false;
        return System.currentTimeMillis() < GuardApi.prefs(context).getLong(KEY_UNTIL, 0L);
    }
}
