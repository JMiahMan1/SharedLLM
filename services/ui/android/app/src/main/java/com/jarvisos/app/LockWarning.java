package com.jarvisos.app;

/**
 * Family Link's one-minute warning (Phase 11 step 5): a minute before
 * bedtime, school time, the end of the day's screen time or an app's own
 * minutes, the child is told, so nothing is cut off mid-sentence. The
 * server names the next lock ({@code next_lock_at}, {@code next_lock_reason});
 * the guard checks the clock every few seconds without a network round trip.
 *
 * Plain Java, JVM-tested.
 */
public final class LockWarning {

    static final long WARN_MS = 60_000L;

    public static final class Warning {
        /** The same lock keeps one key, so the child is warned once. */
        public final String key;
        public final String text;

        Warning(String key, String text) {
            this.key = key;
            this.text = text;
        }
    }

    private LockWarning() {}

    /** A warning when the next lock is a minute away or less (and not yet here), else null. */
    public static Warning due(long nowMs, long nextLockAtMs, String reason) {
        if (nextLockAtMs <= 0 || reason == null || reason.isEmpty()) return null;
        long left = nextLockAtMs - nowMs;
        if (left <= 0 || left > WARN_MS) return null;
        // The limit's lock time moves a little with each status (it is "now + minutes left"),
        // so its key is the ten-minute slot it falls in.
        long slot = "limit".equals(reason) ? nextLockAtMs / 600_000L : nextLockAtMs / 60_000L;
        return new Warning(reason + ":" + slot, text(reason));
    }

    /** One app is down to its last minute (the server counts whole minutes). */
    public static Warning appDue(String pkg, String label, Integer minutesLeft, String day) {
        if (pkg == null || minutesLeft == null || minutesLeft != 1) return null;
        String name = label == null || label.isEmpty() ? "This app" : label;
        return new Warning("app:" + pkg + ":" + day, name + " has one minute left today.");
    }

    /**
     * Bedtime and school start on the clock: once their time has come the
     * phone is locked even before the next status arrives.
     */
    public static boolean lockedByClock(long nowMs, long nextLockAtMs, String reason) {
        return nextLockAtMs > 0 && nowMs >= nextLockAtMs && ("bedtime".equals(reason) || "school".equals(reason));
    }

    static String text(String reason) {
        switch (reason) {
            case "bedtime":
                return "Bedtime in one minute. Time to finish up.";
            case "school":
                return "School time starts in one minute.";
            case "limit":
                return "One minute of screen time left today.";
            default:
                return "Your phone locks in one minute.";
        }
    }
}
