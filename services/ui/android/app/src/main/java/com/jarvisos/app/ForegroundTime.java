package com.jarvisos.app;

import android.app.usage.UsageEvents;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;

/**
 * Folds a clipped {@code UsageStatsManager.queryEvents(start, now)} stream into
 * foreground time per package.
 *
 * <p>This replaces summing {@code getTotalTimeInForeground()} over
 * {@code queryUsageStats(INTERVAL_DAILY, start, now)} buckets. Android expands a
 * daily query to whole-day buckets, so a bucket total can include time from
 * before midnight, or from before the phone was woken, and that time was being
 * reported as "screen time today". The event stream, by contrast, is clipped to
 * exactly the window that was asked for, so a RESUMED to PAUSED stretch can only
 * fall inside it.
 */
final class ForegroundTime {
    private final Map<String, Long> millisByPackage = new HashMap<>();
    private final Map<String, Long> lastUseByPackage = new HashMap<>();
    private long totalMillis = 0;
    private String openPackage = null;
    private long openSince = 0;
    /** The lock screen is up: nothing counts until it is unlocked. */
    private boolean keyguard = false;
    /** The app that came to the front behind the lock screen, which starts counting at unlock. */
    private String behindKeyguard = null;

    /**
     * Record one usage event. A RESUMED event opens a stretch for its package;
     * PAUSED and SCREEN_NON_INTERACTIVE close the open stretch. A RESUMED for a
     * different package also closes the previous stretch.
     *
     * <p>Only real use counts: while the lock screen is showing (KEYGUARD_SHOWN
     * until KEYGUARD_HIDDEN, Android 9+) nothing is counted, even an app that
     * shows over the lock screen or was left open behind it. The app in front
     * at unlock starts counting from the unlock.
     */
    void accept(int eventType, long timestamp, String packageName) {
        if (eventType == UsageEvents.Event.KEYGUARD_SHOWN) {
            if (openPackage != null) behindKeyguard = openPackage;
            closeStretch(timestamp);
            keyguard = true;
        } else if (eventType == UsageEvents.Event.KEYGUARD_HIDDEN) {
            keyguard = false;
            if (behindKeyguard != null && openPackage == null) {
                openPackage = behindKeyguard;
                openSince = timestamp;
            }
            behindKeyguard = null;
        } else if (eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
            closeStretch(timestamp);
            if (keyguard) {
                behindKeyguard = packageName;
                return;
            }
            openPackage = packageName;
            openSince = timestamp;
            if (packageName != null) {
                lastUseByPackage.put(packageName, timestamp);
            }
        } else if (eventType == UsageEvents.Event.ACTIVITY_PAUSED
                || eventType == UsageEvents.Event.SCREEN_NON_INTERACTIVE) {
            if (eventType == UsageEvents.Event.ACTIVITY_PAUSED && keyguard
                    && packageName != null && packageName.equals(behindKeyguard)) {
                behindKeyguard = null;
            }
            if (eventType == UsageEvents.Event.SCREEN_NON_INTERACTIVE) behindKeyguard = null;
            closeStretch(timestamp);
        }
    }

    /** Close any stretch still open at the end of the window. */
    void finish(long endTimestamp) {
        closeStretch(endTimestamp);
    }

    private void closeStretch(long timestamp) {
        if (openPackage != null) {
            long elapsed = timestamp - openSince;
            if (elapsed > 0) {
                millisByPackage.merge(openPackage, elapsed, Long::sum);
                totalMillis += elapsed;
            }
            openPackage = null;
        }
    }

    /** Total foreground milliseconds across every package in the window. */
    long totalMillis() {
        return totalMillis;
    }

    /** Foreground milliseconds for one package (0 when it never appeared). */
    long millisFor(String packageName) {
        Long value = millisByPackage.get(packageName);
        return value == null ? 0 : value;
    }

    /** The last time a package came to the foreground, or null if it never did. */
    Long lastUseFor(String packageName) {
        return lastUseByPackage.get(packageName);
    }

    /** Packages that had foreground time in the window. */
    Set<String> packages() {
        return millisByPackage.keySet();
    }
}
