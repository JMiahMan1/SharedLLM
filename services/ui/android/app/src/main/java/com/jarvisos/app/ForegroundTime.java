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

    /**
     * Record one usage event. A RESUMED event opens a stretch for its package;
     * PAUSED and SCREEN_NON_INTERACTIVE close the open stretch. A RESUMED for a
     * different package also closes the previous stretch.
     */
    void accept(int eventType, long timestamp, String packageName) {
        if (eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
            closeStretch(timestamp);
            openPackage = packageName;
            openSince = timestamp;
            if (packageName != null) {
                lastUseByPackage.put(packageName, timestamp);
            }
        } else if (eventType == UsageEvents.Event.ACTIVITY_PAUSED
                || eventType == UsageEvents.Event.SCREEN_NON_INTERACTIVE) {
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
