package com.jarvisos.app;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

/**
 * The rules that decide which hour a step delta belongs to.
 *
 * The regression these guard: crediting the whole since-boot total to the hour
 * the app happened to be opened in, and crediting a delta measured across a long
 * gap to the single hour that happened to be current when it arrived. Both
 * invent walking that never happened -- a chart that shows a spike at 9 AM
 * because the phone was closed all morning is worse than no hourly chart,
 * because it looks like data.
 *
 * StepHourAccumulator is deliberately free of Android imports so this runs on a
 * plain JVM; the plugin owns only persistence.
 */
public class StepHourAccumulatorTest {

    private static final long T0 = 1_000L;
    private static final long MINUTE = 60_000L;

    private static StepHourAccumulator.State state(
            long baseline, long lastReadAt, boolean hasBaseline) {
        return new StepHourAccumulator.State(baseline, lastReadAt, hasBaseline);
    }

    /** A cold start: the counter has been running since boot and cannot be split. */
    @Test
    public void aFirstReadingCreditsNothing() {
        StepHourAccumulator.State s = state(0L, 0L, false);
        assertEquals(0, StepHourAccumulator.credit(s, 45_000L, T0));
    }

    @Test
    public void aFirstReadingStillAnchorsTheCounter() {
        StepHourAccumulator.State s = state(0L, 0L, false);
        StepHourAccumulator.credit(s, 45_000L, T0);
        assertEquals(45_000L, s.baseline);
    }

    @Test
    public void successiveReadingsCreditTheirDelta() {
        StepHourAccumulator.State s = state(1_000L, T0, true);
        assertEquals(120, StepHourAccumulator.credit(s, 1_120L, T0 + 30_000L));
    }

    @Test
    public void aSecondGapWithinTheWindowCreditsTheNextDelta() {
        StepHourAccumulator.State s = state(1_000L, T0, true);
        StepHourAccumulator.credit(s, 1_120L, T0 + 30_000L);
        assertEquals(80, StepHourAccumulator.credit(s, 1_200L, T0 + 60_000L));
    }

    /**
     * The gap that matters: the app was closed for an hour, so the delta
     * covers an hour nobody can name. It is dropped, leaving the hour
     * unrecorded rather than drawing a spike.
     */
    @Test
    public void aDeltaAcrossALongGapIsNotCreditedToOneHour() {
        long gap = StepHourAccumulator.CREDIT_WINDOW_MS + MINUTE;
        StepHourAccumulator.State s = state(1_000L, T0, true);
        assertEquals(0, StepHourAccumulator.credit(s, 9_000L, T0 + gap));
    }

    @Test
    public void aDroppedDeltaStillReAnchorsSoTheNextReadingIsUsable() {
        long gap = StepHourAccumulator.CREDIT_WINDOW_MS + MINUTE;
        StepHourAccumulator.State s = state(1_000L, T0, true);
        StepHourAccumulator.credit(s, 9_000L, T0 + gap);
        assertEquals(40, StepHourAccumulator.credit(s, 9_040L, T0 + gap + 20_000L));
    }

    /** A reboot lowers the counter; the steps before it are already counted. */
    @Test
    public void aRebootCreditsNothing() {
        StepHourAccumulator.State s = state(12_000L, T0, true);
        assertEquals(0, StepHourAccumulator.credit(s, 20L, T0 + 1_000L));
    }

    @Test
    public void stepsAfterARebootAreCreditedToTheirHour() {
        StepHourAccumulator.State s = state(12_000L, T0, true);
        StepHourAccumulator.credit(s, 20L, T0 + 1_000L);
        assertEquals(100, StepHourAccumulator.credit(s, 120L, T0 + 2_000L));
    }

    /** Reading exactly on the window boundary is still a single hour's delta. */
    @Test
    public void aDeltaOnTheWindowBoundaryIsCredited() {
        StepHourAccumulator.State s = state(1_000L, T0, true);
        assertEquals(
                300,
                StepHourAccumulator.credit(s, 1_300L, T0 + StepHourAccumulator.CREDIT_WINDOW_MS));
    }

    /** A stalled sensor reporting the same number credits nothing. */
    @Test
    public void anUnchangedCounterCreditsNothing() {
        StepHourAccumulator.State s = state(1_000L, T0, true);
        assertEquals(0, StepHourAccumulator.credit(s, 1_000L, T0 + 30_000L));
    }
}