package com.jarvisos.app;

/**
 * Pure arithmetic for splitting the since-boot step counter into hours.
 *
 * The hardware sensor only reports a running total, so "steps in the 2 PM hour"
 * is something the app has to reconstruct from successive readings. Two cases
 * make a naive delta wrong, and both of them invent steps that never happened:
 *
 *  - First reading after a cold start. The counter has been running since boot,
 *    so the whole since-boot total would be credited to whatever hour the user
 *    happened to open the app in.
 *  - A long gap. If the previous reading was an hour ago, the delta covers a
 *    whole hour and cannot honestly be attributed to the current one. It is
 *    dropped instead: the hour is left unrecorded, which the UI draws as
 *    missing data rather than as a quiet hour.
 *
 * A reboot lowers the counter. The pre-reboot steps were already counted, and
 * the hour they belong to is unknowable, so the reading re-anchors the counter
 * and credits nothing.
 *
 * No Android imports on purpose: this class is compiled and run on a desktop
 * JVM in the test harness, like {@link StepDayAccumulator}.
 */
final class StepHourAccumulator {

    /**
     * Only credit a delta when the previous reading is this recent. Beyond it,
     * the delta may span hours, and putting it all in the current hour would
     * draw a spike that never occurred.
     */
    static final long CREDIT_WINDOW_MS = 15L * 60L * 1000L;

    /** Mutable state this class reads and returns; owned by the caller. */
    static final class State {
        long baseline;
        long lastReadAt;
        boolean hasBaseline;

        State(long baseline, long lastReadAt, boolean hasBaseline) {
            this.baseline = baseline;
            this.lastReadAt = lastReadAt;
            this.hasBaseline = hasBaseline;
        }
    }

    private StepHourAccumulator() {}

    /**
     * Fold one cumulative-since-boot reading into the current hour.
     *
     * @param cumulative steps since boot, as the sensor reports it
     * @param now        wall-clock millis for this reading
     * @return the steps to add to the hour that `now` falls in; 0 when the
     *         reading cannot be attributed to a single hour
     */
    static int credit(State state, long cumulative, long now) {
        int credited = 0;
        boolean counterReset = state.hasBaseline && cumulative < state.baseline;
        boolean recentReading =
                state.hasBaseline
                        && state.lastReadAt > 0
                        && (now - state.lastReadAt) <= CREDIT_WINDOW_MS;
        if (state.hasBaseline && !counterReset && recentReading) {
            long delta = cumulative - state.baseline;
            if (delta > 0) {
                credited = (int) Math.min(delta, Integer.MAX_VALUE);
            }
        }
        state.baseline = cumulative;
        state.lastReadAt = now;
        state.hasBaseline = true;
        return credited;
    }
}