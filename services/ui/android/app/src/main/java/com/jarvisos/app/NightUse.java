package com.jarvisos.app;

import android.app.usage.UsageEvents;

import java.util.ArrayList;
import java.util.List;

/**
 * Folds usage events over a night into the stretches the phone was in use: an
 * app came to the front, and the stretch ends when it left or the screen went
 * off. Stretches less than a minute apart are one. The server joins these
 * across a person's devices and reads the long quiet stretch as sleep.
 */
final class NightUse {

    static final long MERGE_MS = 60_000L;
    static final int MAX_STRETCHES = 500;

    private final List<long[]> stretches = new ArrayList<>();
    private long openSince = -1;

    /** Fold one UsageEvents event in (events arrive in time order). */
    void accept(int eventType, long timestamp) {
        if (eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
            if (openSince < 0) {
                openSince = timestamp;
            }
        } else if (eventType == UsageEvents.Event.ACTIVITY_PAUSED
                || eventType == UsageEvents.Event.SCREEN_NON_INTERACTIVE) {
            close(timestamp);
        }
    }

    /** The stretches up to ``now``; one still open ends at ``now``. */
    List<long[]> finish(long now) {
        close(now);
        return stretches;
    }

    private void close(long timestamp) {
        if (openSince < 0) {
            return;
        }
        long start = openSince;
        long end = Math.max(timestamp, start);
        openSince = -1;
        if (!stretches.isEmpty()) {
            long[] last = stretches.get(stretches.size() - 1);
            if (start - last[1] <= MERGE_MS) {
                last[1] = Math.max(last[1], end);
                return;
            }
        }
        if (stretches.size() >= MAX_STRETCHES) {
            stretches.remove(0);
        }
        stretches.add(new long[] {start, end});
    }
}
