package com.jarvisos.app.widgets;

/**
 * The arithmetic behind the health widget's progress ring, kept free of Android
 * types so it can be unit-tested on a desktop JVM.
 *
 * Every rule here exists because the alternative quietly lies. A ring is a
 * visual claim -- "you are this far toward your goal" -- so a bad percentage
 * does not look like a bug, it looks like a fact about your day.
 */
public final class HealthRingCalc {

    /** No Android import: a plain result the widget maps onto RemoteViews. */
    public static final class Ring {
        /** 0-100, already clamped, or -1 when there is no ring to draw. */
        public final int percent;
        /** True when a goal exists and progress can be shown at all. */
        public final boolean hasGoal;
        /** What the ring is measuring, e.g. "4,210". */
        public final String valueText;
        /** The line under the ring, already chosen for this state. */
        public final String caption;

        Ring(int percent, boolean hasGoal, String valueText, String caption) {
            this.percent = percent;
            this.hasGoal = hasGoal;
            this.valueText = valueText;
            this.caption = caption;
        }
    }

    private HealthRingCalc() {}

    /**
     * Percentage of the goal reached, clamped to 0-100.
     *
     * Clamping matters in both directions: exceeding the goal must not draw a
     * ring past full, and a negative reading (a counter reset mid-day) must not
     * draw backwards. A goal of zero or less means "no goal", not "infinite
     * percent" -- returning 0 keeps the caller from dividing by zero.
     */
    public static int percent(int steps, int goal) {
        if (goal <= 0) return 0;
        if (steps <= 0) return 0;
        if (steps >= goal) return 100;
        return (int) Math.round((steps * 100.0) / goal);
    }

    /**
     * Build the ring state for the widget.
     *
     * The three no-data cases are distinguished rather than collapsed into a
     * blank ring, because they mean different things to the reader and the
     * app already tells them apart:
     *   - never recorded: nothing has ever been synced
     *   - no reading today: the phone has not reported yet, which is normal
     *   - no goal set: tracking fine, just nothing to measure against
     */
    public static Ring build(int steps, int goal, String formatted, boolean everRecorded) {
        if (!everRecorded) {
            return new Ring(-1, false, "—", "No steps recorded yet");
        }
        if (goal <= 0) {
            // Steps are known; only the target is missing. Show the number and
            // say the goal is unset rather than drawing an empty ring.
            return new Ring(-1, false, formatted, "No step goal set");
        }
        return new Ring(percent(steps, goal), true, formatted, formatted + " of " + goal + " (" + percent(steps, goal) + "%)");
    }
}
