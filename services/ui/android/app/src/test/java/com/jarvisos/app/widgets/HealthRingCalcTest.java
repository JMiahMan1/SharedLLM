package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** Desktop-JVM tests for the health widget's ring arithmetic. */
public class HealthRingCalcTest {

    @Test
    public void percentOfGoalIsRounded() {
        assertEquals(50, HealthRingCalc.percent(5000, 10000));
        // 4231/10000 = 42.31 -> 42, not 42.3 and not 43.
        assertEquals(42, HealthRingCalc.percent(4231, 10000));
        assertEquals(1, HealthRingCalc.percent(60, 10000));
    }

    @Test
    public void exceedingTheGoalClampsToFull() {
        // A ring drawn past 100% is a rendering bug that reads as a fact.
        assertEquals(100, HealthRingCalc.percent(25000, 10000));
        assertEquals(100, HealthRingCalc.percent(10000, 10000));
    }

    @Test
    public void aNegativeReadingDoesNotDrawBackwards() {
        // A counter reset mid-day can report below zero; the ring must not.
        assertEquals(0, HealthRingCalc.percent(-50, 10000));
    }

    @Test
    public void aMissingGoalIsNotInfinitePercent() {
        // Dividing by a zero goal must not throw or produce NaN.
        assertEquals(0, HealthRingCalc.percent(5000, 0));
        assertEquals(0, HealthRingCalc.percent(5000, -1));
    }

    @Test
    public void zeroStepsAgainstAGoalIsAnEmptyRingNotNoGoal() {
        HealthRingCalc.Ring r = HealthRingCalc.build(0, 10000, "0", true);
        assertTrue("a goal exists, so the ring should still draw", r.hasGoal);
        assertEquals(0, r.percent);
    }

    @Test
    public void neverRecordedIsDistinctFromNoReadingToday() {
        // Both are "no number", but they mean different things and collapsing
        // them tells the reader their tracker has never worked.
        HealthRingCalc.Ring never = HealthRingCalc.build(0, 10000, "—", false);
        assertEquals(-1, never.percent);
        assertEquals("No steps recorded yet", never.caption);

        HealthRingCalc.Ring quiet = HealthRingCalc.build(0, 10000, "0", true);
        assertEquals(0, quiet.percent);
        assertTrue(quiet.hasGoal);
    }

    @Test
    public void noGoalKeepsTheNumberAndSaysTheGoalIsMissing() {
        HealthRingCalc.Ring r = HealthRingCalc.build(4200, 0, "4,200", true);
        assertEquals(-1, r.percent);
        assertEquals("No step goal set", r.caption);
        assertEquals("4,200", r.valueText);
    }

    @Test
    public void withAGoalTheRingReportsProgress() {
        HealthRingCalc.Ring r = HealthRingCalc.build(4210, 10000, "4,210", true);
        assertEquals(42, r.percent);
        assertEquals("4,210 of 10000 (42%)", r.caption);
    }
}
