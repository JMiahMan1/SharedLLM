package com.jarvisos.app;

/**
 * Pure step-count arithmetic, split out of StepCounterPlugin so it can be
 * tested without an Android device.
 *
 * Android's TYPE_STEP_COUNTER reports steps since boot. Turning that into
 * "steps today" needs a baseline and two special cases, and getting either
 * special case wrong silently loses a day's walking:
 *
 *  - Reboot: the counter drops. The day's total must be KEPT and the baseline
 *    re-anchored to the new counter. A previous version cleared the baseline
 *    flag, fell through to the midnight branch, and set the day to 0 -- so
 *    every reboot threw away the day. Installing an app update reboots the
 *    phone, which is exactly when a user is least likely to notice.
 *  - Midnight: the counter does not reset, so the only honest option is the
 *    delta since the last reading -- and only when that reading was recent
 *    enough that the delta cannot contain a whole unrecorded day.
 *
 * No Android imports on purpose: this class is compiled and run on a desktop
 * JVM in the test harness.
 */
final class StepDayAccumulator {

    /**
     * Only credit an across-midnight delta when the previous reading was this
     * recent. A longer gap can contain an entire unrecorded day, and
     * attributing that to today would inflate today's count.
     */
    static final long MIDNIGHT_CREDIT_WINDOW_MS = 60L * 60L * 1000L;

    /** Mutable state the accumulator reads and returns; owned by the caller. */
    static final class State {
        long baseline;
        String baselineDate = "";
        boolean hasBaseline;
        long daySteps;
        long lastReadAt;

        State(long baseline, String baselineDate, boolean hasBaseline, long daySteps, long lastReadAt) {
            this.baseline = baseline;
            this.baselineDate = baselineDate;
            this.hasBaseline = hasBaseline;
            this.daySteps = daySteps;
            this.lastReadAt = lastReadAt;
        }
    }

    private StepDayAccumulator() {}

    /**
     * Fold one cumulative-since-boot reading into the running day total.
     *
     * @param cumulative steps since boot, as the sensor reports it
     * @param today      the local date the reading belongs to (yyyy-MM-dd)
     * @param now        wall-clock millis for this reading
     * @return the day's total after folding the reading in
     */
    static long accumulate(State state, long cumulative, String today, long now) {
        boolean counterReset = state.hasBaseline && cumulative < state.baseline;

        if (state.hasBaseline && state.baselineDate.equals(today)) {
            // Same day, so the day's total stands. A reboot only invalidates
            // the reference point, not what we already counted.
            if (counterReset) {
                // Re-anchor onto the post-reboot counter and keep the day.
                state.baseline = cumulative;
                state.lastReadAt = now;
                return state.daySteps;
            }
            long delta = cumulative - state.baseline;
            if (delta > 0) {
                state.daySteps += delta;
            }
            state.baseline = cumulative;
            state.lastReadAt = now;
            return state.daySteps;
        }

        // A new day, or no usable baseline yet.
        boolean recentReading =
                state.lastReadAt > 0 && (now - state.lastReadAt) <= MIDNIGHT_CREDIT_WINDOW_MS;
        if (state.hasBaseline
                && !state.baselineDate.equals(today)
                && recentReading
                && !counterReset
                && cumulative >= state.baseline) {
            // Only the across-midnight delta -- never yesterday's total.
            // Skipped when the counter reset, because the pre-reboot total is
            // unknowable and the new counter starts from zero.
            state.daySteps = cumulative - state.baseline;
        } else {
            state.daySteps = 0;
        }
        state.baseline = cumulative;
        state.baselineDate = today;
        state.hasBaseline = true;
        state.lastReadAt = now;
        return state.daySteps;
    }
}
