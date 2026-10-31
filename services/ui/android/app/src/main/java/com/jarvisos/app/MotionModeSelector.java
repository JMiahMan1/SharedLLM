package com.jarvisos.app;

/**
 * What the phone is doing (driving, cycling, walking, still), which sets how
 * often {@link LocationTrackingService} asks for a fix and uploads one.
 *
 * One fixed rate cannot serve both ends: a fix every 30 s cut a 1.4 mile drive
 * down to a two-minute trip, while a fix every second on the nightstand would
 * drain the battery for nothing. So Activity Recognition says what the person
 * is doing when Google Play services can tell, and speed says it when it
 * cannot (no permission, no Play services, or Activity Recognition is slow to
 * notice the car): a GPS fix at 7 m/s or more is driving, and three minutes
 * with no fix of 2 m/s or more ends it.
 *
 * No Android types here, so the rules can be tested without a device.
 */
final class MotionModeSelector {
    /** A fix at this speed or more (about 16 mph) means a vehicle; few people run or walk it. */
    static final double ENTER_DRIVING_MPS = 7.0;
    /** Slower than this (about 4.5 mph) is walking pace or a stop. */
    static final double LEAVE_DRIVING_MPS = 2.0;
    /** Driving ends after this long with no fix at {@link #LEAVE_DRIVING_MPS} or more. */
    static final long LEAVE_DRIVING_AFTER_MS = 3 * 60 * 1000L;
    /** A fix less precise than this is a network or poor GPS fix; its speed is not trusted. */
    static final double SPEED_FIX_MAX_ACCURACY_M = 25.0;

    enum Mode {
        /** A fix every second, any distance, so a drive keeps its shape and its stops. */
        DRIVING("driving", true, 1_000L, 0f, 15_000L),
        CYCLING("cycling", true, 15_000L, 5f, 30_000L),
        /** Walking and running. */
        WALKING("walking", true, 15_000L, 5f, 30_000L),
        /** Wi-Fi and cell positioning once a minute: no GPS for someone sitting still. */
        STILL("still", false, 60_000L, 5f, 30_000L),
        /** Nothing known yet: GPS every 30 s, as before modes, so speed can still find a drive. */
        UNKNOWN("unknown", true, 30_000L, 5f, 30_000L);

        /** Sent with each fix as {@code activity}. */
        final String activity;
        /** GPS-grade fixes (Play services' high accuracy priority) rather than balanced power. */
        final boolean highAccuracy;
        final long intervalMs;
        final float minDistanceM;
        /** At most one upload this often while fixes keep arriving. */
        final long uploadEveryMs;

        Mode(String activity, boolean highAccuracy, long intervalMs, float minDistanceM, long uploadEveryMs) {
            this.activity = activity;
            this.highAccuracy = highAccuracy;
            this.intervalMs = intervalMs;
            this.minDistanceM = minDistanceM;
            this.uploadEveryMs = uploadEveryMs;
        }

        /** The persisted name back to a mode; anything unreadable is {@link #UNKNOWN}. */
        static Mode parse(String name) {
            if (name == null) return UNKNOWN;
            try {
                return Mode.valueOf(name);
            } catch (IllegalArgumentException e) {
                return UNKNOWN;
            }
        }
    }

    private Mode mode;
    /** Fix time of the last trusted fix at walking pace or faster. */
    private long lastMovingAt;

    MotionModeSelector(Mode mode, long lastMovingAt) {
        this.mode = mode == null ? Mode.UNKNOWN : mode;
        this.lastMovingAt = lastMovingAt;
    }

    Mode mode() {
        return mode;
    }

    long lastMovingAt() {
        return lastMovingAt;
    }

    /**
     * An Activity Recognition transition. An enter switches to that mode; an
     * exit of the current one falls back to {@link Mode#UNKNOWN} until the
     * next enter (normally moments later). Returns whether the mode changed.
     */
    boolean onTransition(Mode activity, boolean enter, long nowMs) {
        if (activity == null || activity == Mode.UNKNOWN) return false;
        Mode before = mode;
        if (enter) {
            mode = activity;
            // The three-minute rule counts from here, not from a walk earlier.
            if (activity == Mode.DRIVING && before != Mode.DRIVING) lastMovingAt = nowMs;
        } else if (activity == mode) {
            mode = Mode.UNKNOWN;
        }
        return mode != before;
    }

    /**
     * A fix. {@code speedMps} is NaN when the fix has no speed (a network
     * fix); such a fix neither starts nor extends a drive. Returns whether the
     * mode changed.
     */
    boolean onFix(long timeMs, double speedMps, double accuracyM) {
        Mode before = mode;
        boolean trusted = !Double.isNaN(speedMps) && !Double.isNaN(accuracyM)
                && accuracyM <= SPEED_FIX_MAX_ACCURACY_M;
        if (trusted && speedMps >= LEAVE_DRIVING_MPS && timeMs > lastMovingAt) lastMovingAt = timeMs;
        // Cycling is the one Activity Recognition answer kept at speed: a fast
        // descent on a bike is not a drive.
        if (trusted && speedMps >= ENTER_DRIVING_MPS && mode != Mode.DRIVING && mode != Mode.CYCLING) {
            mode = Mode.DRIVING;
            lastMovingAt = timeMs;
        }
        tick(timeMs);
        return mode != before;
    }

    /**
     * Ends a drive that has been slow (or silent) for three minutes; called
     * with each fix and from the heartbeat, since a parked phone indoors may
     * send no fix at all. Returns whether the mode changed.
     */
    boolean tick(long nowMs) {
        if (mode == Mode.DRIVING && nowMs - lastMovingAt >= LEAVE_DRIVING_AFTER_MS) {
            mode = Mode.UNKNOWN;
            return true;
        }
        return false;
    }

    /**
     * Activity Recognition is no longer delivering (permission taken back, Play
     * services gone). Still, walking and cycling only ever came from it, and
     * still in particular asks for no GPS, so speed could never end it: fall
     * back to {@link Mode#UNKNOWN}. Returns whether the mode changed.
     */
    boolean dropActivityRecognition() {
        if (mode == Mode.STILL || mode == Mode.WALKING || mode == Mode.CYCLING) {
            mode = Mode.UNKNOWN;
            return true;
        }
        return false;
    }
}
