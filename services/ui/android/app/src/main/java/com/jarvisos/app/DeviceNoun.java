package com.jarvisos.app;

import android.content.Context;

/**
 * "tablet" or "phone", for text that names this device. Android's rule, the
 * same one {@link DeviceInfoPlugin} reports: a smallest screen width of 600dp
 * and up is a tablet. An unreadable width (0) reads as a phone.
 */
final class DeviceNoun {

    static final int TABLET_MIN_DP = 600;

    private DeviceNoun() {}

    static String of(Context context) {
        return forWidth(context.getResources().getConfiguration().smallestScreenWidthDp);
    }

    /** Plain Java, JVM-tested. */
    static String forWidth(int smallestWidthDp) {
        return smallestWidthDp >= TABLET_MIN_DP ? "tablet" : "phone";
    }
}
