package com.jarvisos.app;

import java.util.Collection;
import java.util.Set;

/**
 * The plain decisions behind keeping collection alive (no Android types, so
 * they are unit tested): which notifications mean a call or text just
 * happened, how often a notification may start a comms run, and which
 * WorkManager states still count as scheduled.
 */
final class CollectRules {
    /** A notification-triggered comms run starts at most this often. */
    static final long LISTENER_RUN_GAP_MS = 30_000L;
    /** The listener re-arms the periodic workers at most this often while it runs. */
    static final long REARM_GAP_MS = 60L * 60 * 1000;

    private CollectRules() {}

    /**
     * Whether a notification from {@code pkg} means a call or text probably
     * just landed in the call log or the SMS store: the default SMS app, the
     * default dialer, or one of the known messaging apps.
     */
    static boolean isCommsSource(String pkg, String defaultSms, String defaultDialer, Set<String> messagingApps) {
        if (pkg == null || pkg.isEmpty()) return false;
        if (pkg.equals(defaultSms) || pkg.equals(defaultDialer)) return true;
        return messagingApps != null && messagingApps.contains(pkg);
    }

    /** WorkManager's states that mean the periodic work will run again. */
    static boolean isScheduled(String state) {
        return "ENQUEUED".equals(state) || "RUNNING".equals(state) || "BLOCKED".equals(state);
    }

    /**
     * One state for a unique work name: a scheduled one if any is (a replaced
     * request leaves a CANCELLED entry beside the live one), else the last
     * reported, else NONE (never scheduled, or pruned).
     */
    static String summarize(Collection<String> states) {
        String last = "NONE";
        if (states == null) return last;
        for (String s : states) {
            if (s == null) continue;
            if (isScheduled(s)) return s;
            last = s;
        }
        return last;
    }

    /**
     * At most one run in flight, and none sooner than {@code gapMs} after the
     * previous one started. Thread-safe; the clock is passed in so it is testable.
     */
    static final class Gate {
        private final long gapMs;
        private boolean running;
        private long lastStart = Long.MIN_VALUE;

        Gate(long gapMs) {
            this.gapMs = gapMs;
        }

        /** True when the caller may start now (and must call {@link #finish}). */
        synchronized boolean tryStart(long nowMs) {
            if (running) return false;
            if (lastStart != Long.MIN_VALUE && nowMs - lastStart < gapMs && nowMs >= lastStart) return false;
            running = true;
            lastStart = nowMs;
            return true;
        }

        synchronized void finish() {
            running = false;
        }

        synchronized boolean isRunning() {
            return running;
        }
    }
}
