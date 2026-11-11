package com.jarvisos.app;

import java.util.HashSet;
import java.util.Set;

/**
 * Warnings before a lock (Phase 11 step 5): ten minutes and five minutes
 * before bedtime, school time, "chores first" or the end of the day's screen
 * time, the child gets a dialog that says how long is left and offers to ask
 * a parent for more time; a minute before, a last heads-up notice. The server
 * names the next lock ({@code next_lock_at}, {@code next_lock_reason}); the
 * guard checks the clock every few seconds without a network round trip.
 *
 * Plain Java, JVM-tested.
 */
public final class LockWarning {

    /** The warnings, largest first. The ones at or above {@link #DIALOG_MIN} get a dialog. */
    static final int[] STAGES_MIN = {10, 5, 1};
    static final int DIALOG_MIN = 5;
    static final long WARN_MS = STAGES_MIN[0] * 60_000L;
    /**
     * The warnings start over only once the lock is this far away again (more
     * time was given, or a new day). Wider than the first warning, so a
     * limit whose lock time wobbles by a minute between statuses is not
     * warned about twice.
     */
    static final long RESET_MS = 15 * 60_000L;

    public static final class Warning {
        /** One key per lock reason and stage, so each stage is shown once. */
        public final String key;
        /** Short: "10 minutes left". */
        public final String title;
        public final String text;
        /** Whole minutes left, rounded up. */
        public final int minutes;
        /** The stage this belongs to (10, 5 or 1). */
        public final int stage;

        Warning(String key, String title, String text, int minutes, int stage) {
            this.key = key;
            this.title = title;
            this.text = text;
            this.minutes = minutes;
            this.stage = stage;
        }

        /** Ten and five minutes out: a dialog with "Ask for more time". The last minute is a notice. */
        public boolean dialog() {
            return stage >= DIALOG_MIN;
        }
    }

    private LockWarning() {}

    /**
     * The warning for the stage the clock is in when the next lock is ten
     * minutes away or less (and not yet here), else null. A guard that first
     * hears of a lock seven minutes out warns for the ten-minute stage, with
     * the real seven minutes in the text.
     */
    public static Warning due(long nowMs, long nextLockAtMs, String reason, String device) {
        if (nextLockAtMs <= 0 || reason == null || reason.isEmpty()) return null;
        long left = nextLockAtMs - nowMs;
        if (left <= 0 || left > WARN_MS) return null;
        int minutes = (int) ((left + 59_999L) / 60_000L);
        int stage = STAGES_MIN[0];
        for (int s : STAGES_MIN) {
            if (minutes <= s) stage = s;
        }
        return new Warning(reason + ":" + stage, title(minutes), text(reason, minutes, device), minutes, stage);
    }

    /**
     * Which warnings were already shown for the lock coming up, so each stage
     * is shown once. Forgets them when the lock moves well away (a parent gave
     * more time) or goes (it landed, or there is none).
     */
    public static final class Tracker {
        private final Set<String> shown = new HashSet<>();

        /** The warning to show now, or null (none due, or already shown). */
        public Warning next(long nowMs, long nextLockAtMs, String reason, String device) {
            if (nextLockAtMs <= 0 || nextLockAtMs - nowMs > RESET_MS || nextLockAtMs <= nowMs) {
                shown.clear();
                return null;
            }
            Warning w = due(nowMs, nextLockAtMs, reason, device);
            if (w == null || !shown.add(w.key)) return null;
            // A later stage seen first stands in for the earlier ones.
            for (int s : STAGES_MIN) {
                if (s > w.stage) shown.add(reason + ":" + s);
            }
            return w;
        }

        /** A key the caller already showed some other way (an app's last minute). */
        public boolean markShown(String key) {
            return shown.add(key);
        }
    }

    /** One app is down to its last minute (the server counts whole minutes). */
    public static Warning appDue(String pkg, String label, Integer minutesLeft, String day) {
        if (pkg == null || minutesLeft == null || minutesLeft != 1) return null;
        String name = label == null || label.isEmpty() ? "This app" : label;
        return new Warning("app:" + pkg + ":" + day, "One minute", name + " has one minute left today.", 1, 1);
    }

    /**
     * Bedtime and school start on the clock, and so does "chores first" once
     * the time a parent gave runs out: once their time has come the phone is
     * locked even before the next status arrives.
     */
    public static boolean lockedByClock(long nowMs, long nextLockAtMs, String reason) {
        return nextLockAtMs > 0 && nowMs >= nextLockAtMs
                && ("bedtime".equals(reason) || "school".equals(reason) || "chores".equals(reason));
    }

    static String title(int minutes) {
        return minutes <= 1 ? "One minute" : minutes + " minutes left";
    }

    static String text(String reason, int minutes, String device) {
        String in = minutes <= 1 ? "one minute" : minutes + " minutes";
        switch (reason) {
            case "bedtime":
                return "Bedtime in " + in + ". Time to finish up.";
            case "school":
                return "School time starts in " + in + ".";
            case "limit":
                return (minutes <= 1 ? "One minute" : minutes + " minutes") + " of screen time left today.";
            case "chores":
                return (minutes <= 1 ? "One minute" : minutes + " minutes") + " left. Then it's chores first.";
            default:
                return "Your " + device + " locks in " + in + ".";
        }
    }
}
