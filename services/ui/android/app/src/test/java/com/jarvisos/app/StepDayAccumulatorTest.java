package com.jarvisos.app;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

/**
 * The reboot / midnight / delta rules for steps-today.
 *
 * The regression these guard: a reboot used to clear the baseline flag, which
 * dropped execution into the midnight-rollover branch whose else clause set the
 * day to 0. So every reboot discarded the day's steps -- and installing an app
 * update reboots the phone, which is exactly when a user would not connect the
 * zeros to anything they did.
 *
 * StepDayAccumulator is deliberately free of Android imports so this runs on a
 * plain JVM; the plugin owns only persistence.
 */
public class StepDayAccumulatorTest {

    private static final String DAY = "2026-10-01";
    private static final String NEXT_DAY = "2026-10-02";
    private static final long HOUR = 3600_000L;
    private static final long T0 = 1_000L;

    private static StepDayAccumulator.State state(
            long baseline, String date, boolean hasBaseline, long daySteps, long lastReadAt) {
        return new StepDayAccumulator.State(baseline, date, hasBaseline, daySteps, lastReadAt);
    }

    /** A reboot: the since-boot counter drops back towards zero. */
    @Test
    public void aRebootKeepsTheStepsAlreadyWalkedToday() {
        StepDayAccumulator.State s = state(12_000L, DAY, true, 5_000L, T0);
        assertEquals(5_000L, StepDayAccumulator.accumulate(s, 20L, DAY, T0 + 1000L));
    }

    @Test
    public void aRebootReAnchorsTheBaselineOntoTheNewCounter() {
        StepDayAccumulator.State s = state(12_000L, DAY, true, 5_000L, T0);
        StepDayAccumulator.accumulate(s, 20L, DAY, T0 + 1000L);
        assertEquals(20L, s.baseline);
    }

    @Test
    public void stepsAfterARebootAddToThePreservedTotal() {
        StepDayAccumulator.State s = state(20L, DAY, true, 5_000L, T0 + 1000L);
        assertEquals(5_100L, StepDayAccumulator.accumulate(s, 120L, DAY, T0 + 2000L));
    }

    /** The real-world shape: a long day, then an app update reboots the phone. */
    @Test
    public void aRebootLateInTheDayKeepsALargeTotal() {
        StepDayAccumulator.State s = state(45_000L, DAY, true, 18_234L, 500_000L);
        assertEquals(18_234L, StepDayAccumulator.accumulate(s, 5L, DAY, 600_000L));
    }

    @Test
    public void sameDayDeltaAccumulates() {
        StepDayAccumulator.State s = state(1_000L, DAY, true, 4_000L, T0);
        assertEquals(4_500L, StepDayAccumulator.accumulate(s, 1_500L, DAY, T0 + 1000L));
    }

    @Test
    public void repeatedUnchangedReadingsDoNotInflateTheDay() {
        StepDayAccumulator.State s = state(1_000L, DAY, true, 4_000L, T0);
        long v = StepDayAccumulator.accumulate(s, 1_000L, DAY, T0 + 1000L);
        v = StepDayAccumulator.accumulate(s, 1_000L, DAY, T0 + 2000L);
        v = StepDayAccumulator.accumulate(s, 1_000L, DAY, T0 + 3000L);
        assertEquals(4_000L, v);
    }

    /**
     * The first reading of an install must not report everything since boot as
     * today's steps.
     */
    @Test
    public void theFirstReadingStartsTheDayAtZero() {
        StepDayAccumulator.State s = state(0L, "", false, 0L, 0L);
        assertEquals(0L, StepDayAccumulator.accumulate(s, 777L, DAY, 5_000L));
    }

    @Test
    public void acrossMidnightCreditsOnlyTheDelta() {
        StepDayAccumulator.State s = state(9_000L, DAY, true, 7_000L, 90_000L);
        long now = 90_000L + 60_000L;
        assertEquals(250L, StepDayAccumulator.accumulate(s, 9_250L, NEXT_DAY, now));
    }

    /**
     * A long gap can contain a whole unrecorded day; crediting it to today
     * would inflate today, which is the bug users hit after a day without syncing.
     */
    @Test
    public void aDayLongGapCreditsNothing() {
        StepDayAccumulator.State s = state(9_000L, DAY, true, 7_000L, 0L);
        long now = 90_000L + 20 * HOUR;
        assertEquals(0L, StepDayAccumulator.accumulate(s, 40_000L, NEXT_DAY, now));
    }

    /** After a reboot the pre-reboot total is unknowable, so the day starts clean. */
    @Test
    public void aRebootAcrossMidnightStartsClean() {
        StepDayAccumulator.State s = state(9_000L, DAY, true, 7_000L, 1_000L);
        assertEquals(0L, StepDayAccumulator.accumulate(s, 30L, NEXT_DAY, 2_000L));
    }

    @Test
    public void aNewDayResetsTheTotal() {
        StepDayAccumulator.State s = state(5_000L, DAY, true, 5_000L, 0L);
        long rolled = StepDayAccumulator.accumulate(s, 5_000L, NEXT_DAY, 90_000L + 20 * HOUR);
        assertEquals(0L, rolled);
    }
}
