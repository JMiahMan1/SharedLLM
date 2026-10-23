package com.jarvisos.app;

import android.app.usage.UsageEvents;

/**
 * Folds today's usage events into the window a person actually used the phone:
 * the first foreground, the last foreground, and how many separate stretches
 * (sessions) it happened in. Consecutive foregrounds without a pause in
 * between count once.
 */
final class UsageWindow {

    long firstUse = 0;
    long lastUse = 0;
    int sessions = 0;

    private boolean sessionOpen = false;

    /** Fold one UsageEvents event into the window. */
    void accept(int eventType, long timestamp) {
        if (eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
            if (firstUse == 0 || timestamp < firstUse) {
                firstUse = timestamp;
            }
            if (timestamp > lastUse) {
                lastUse = timestamp;
            }
            if (!sessionOpen) {
                sessions++;
                sessionOpen = true;
            }
        } else if (eventType == UsageEvents.Event.ACTIVITY_PAUSED) {
            sessionOpen = false;
        }
    }

    boolean hasData() {
        return sessions > 0;
    }
}
