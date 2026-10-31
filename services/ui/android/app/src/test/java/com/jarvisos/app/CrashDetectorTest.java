package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public class CrashDetectorTest {
    private static final long T0 = 1_760_000_000_000L;
    private static final double LAT = 35.0;
    private static final double LON = -80.0;
    private static final double G = CrashDetector.GRAVITY;

    private final CrashDetector detector = new CrashDetector();

    private CrashDetector.Result fix(long t, double speed) {
        return detector.onFix(T0 + t, speed, 5, LAT, LON);
    }

    /** One sample whose magnitude is gravity plus {@code beyond} m/s². */
    private void accel(long t, double beyond) {
        detector.onAccel(T0 + t, 0, 0, G + beyond);
    }

    /** Driving at 20 m/s (45 mph), a fix a second up to {@code untilMs}. */
    private void drive(long untilMs) {
        detector.setDriving(true);
        for (long t = 0; t <= untilMs; t += 1000) assertNull(fix(t, 20));
    }

    /** Stopped from {@code fromMs}, a fix a second; returns the first result. */
    private CrashDetector.Result stopFrom(long fromMs, long forMs) {
        CrashDetector.Result r = null;
        for (long t = fromMs; t <= fromMs + forMs; t += 1000) {
            CrashDetector.Result got = fix(t, 0.3);
            if (r == null) r = got;
        }
        return r;
    }

    // Impact then stop.

    @Test
    public void impactThenStopIsACrash() {
        drive(10_000);
        accel(10_500, 45);  // about 4.6 g
        assertTrue(detector.isPending());
        CrashDetector.Result r = stopFrom(13_000, 15_000);
        assertNotNull(r);
        assertFalse(detector.isPending());
        assertEquals(4.6, r.peakG, 1e-9);
        assertEquals(44.7, r.speedBeforeMph, 1e-9);
        assertEquals(T0 + 10_500, r.detectedAtMs);
        // Confirmed by the fix 10 s into the stop.
        assertEquals(T0 + 23_000, r.positionAtMs);
        assertEquals(LAT, r.lat, 1e-9);
        assertEquals(5.0, r.accuracyM, 1e-9);
    }

    @Test
    public void notConfirmedBeforeTenSecondsStopped() {
        drive(10_000);
        accel(10_500, 45);
        assertNull(stopFrom(13_000, 9_000));
        assertTrue(detector.isPending());
        assertNotNull(fix(23_000, 0.3));
    }

    @Test
    public void laterJoltsRaiseThePeak() {
        drive(10_000);
        accel(10_500, 45);
        accel(10_600, 70);
        CrashDetector.Result r = stopFrom(13_000, 10_000);
        assertNotNull(r);
        assertEquals(CrashDetector.round1(70 / G), r.peakG, 1e-9);
    }

    @Test
    public void speedBetweenStoppedAndStillStoppedKeepsTheHold() {
        drive(10_000);
        accel(10_500, 45);
        fix(12_000, 1.0);
        for (long t = 13_000; t < 22_000; t += 1000) assertNull(fix(t, 2.5));
        assertNotNull(fix(22_000, 2.5));
    }

    @Test
    public void bodyHasTheAgreedFields() throws Exception {
        drive(10_000);
        accel(10_500, 45);
        CrashDetector.Result r = stopFrom(13_000, 10_000);
        JSONObject body = r.toBody(true);
        assertTrue(body.getBoolean("auto"));
        assertEquals(44.7, body.getDouble("speed_before_mph"), 1e-9);
        assertEquals(4.6, body.getDouble("peak_g"), 1e-9);
        assertEquals(T0 + 10_500, body.getLong("detected_at"));
        JSONObject position = body.getJSONObject("position");
        assertEquals(LAT, position.getDouble("lat"), 1e-9);
        assertEquals(LON, position.getDouble("lon"), 1e-9);
        assertEquals(5.0, position.getDouble("accuracy"), 1e-9);
        assertEquals(T0 + 23_000, position.getLong("at"));
        assertFalse(r.toBody(false).getBoolean("auto"));
    }

    @Test
    public void keptCrashReadsBackTheSame() throws Exception {
        drive(10_000);
        accel(10_500, 45);
        CrashDetector.Result r = stopFrom(13_000, 10_000);
        CrashDetector.Result back = CrashDetector.Result.fromJson(new JSONObject(r.toJson().toString()));
        assertEquals(r.toBody(true).toString(), back.toBody(true).toString());
    }

    // Not crashes.

    @Test
    public void speedRecoveringMeansNoCrash() {
        drive(10_000);
        accel(10_500, 45);  // a pothole, or the phone hitting the floor
        fix(11_000, 18);
        fix(12_000, 19);
        assertFalse(detector.isPending());
        assertNull(stopFrom(13_000, 15_000));
    }

    @Test
    public void speedRecoveringDuringTheHoldMeansNoCrash() {
        drive(10_000);
        accel(10_500, 45);
        stopFrom(12_000, 5_000);  // stopped at a light
        assertNull(fix(18_000, 8));
        assertFalse(detector.isPending());
        assertNull(stopFrom(19_000, 15_000));
    }

    @Test
    public void noStopWithinTwentySecondsMeansNoCrash() {
        drive(10_000);
        accel(10_500, 45);
        // Slowing but never stopped within 20 s.
        for (long t = 11_000; t <= 30_000; t += 1000) fix(t, 4);
        assertNull(fix(31_000, 0.3));
        assertFalse(detector.isPending());
    }

    @Test
    public void impactWhileNotDrivingIsIgnored() {
        drive(10_000);
        detector.setDriving(false);
        accel(10_500, 60);
        assertFalse(detector.isPending());
    }

    @Test
    public void impactAtLowSpeedIsIgnored() {
        detector.setDriving(true);
        for (long t = 0; t <= 10_000; t += 1000) fix(t, 5);
        accel(10_500, 60);
        assertFalse(detector.isPending());
    }

    @Test
    public void fastSpeedMustBeRecent() {
        detector.setDriving(true);
        fix(0, 20);
        for (long t = 1000; t <= 12_000; t += 1000) fix(t, 4);
        accel(12_000, 60);  // the last fast fix was 12 s ago
        assertFalse(detector.isPending());
    }

    @Test
    public void drivingWithNoFixYetIsNotArmed() {
        detector.setDriving(true);
        accel(0, 60);
        assertFalse(detector.isPending());
    }

    // Spike and sustained.

    @Test
    public void singleSpikeBelowFourGIsNotAnImpact() {
        drive(10_000);
        accel(10_500, 38);
        assertFalse(detector.isPending());
    }

    @Test
    public void singleFourGSpikeIsAnImpact() {
        drive(10_000);
        accel(10_500, 39);
        assertTrue(detector.isPending());
    }

    @Test
    public void threeSustainedSamplesAreAnImpact() {
        drive(10_000);
        accel(10_500, 26);
        accel(10_520, 27);
        assertFalse(detector.isPending());
        accel(10_540, 26);
        assertTrue(detector.isPending());
    }

    @Test
    public void twoSustainedSamplesAreNot() {
        drive(10_000);
        accel(10_500, 26);
        accel(10_520, 27);
        accel(10_540, 5);
        accel(10_560, 26);
        assertFalse(detector.isPending());
    }

    @Test
    public void sustainedSamplesSpreadPastAHundredMsAreNot() {
        drive(10_000);
        accel(10_500, 26);
        accel(10_560, 27);
        accel(10_620, 26);  // the first is 120 ms back
        assertFalse(detector.isPending());
    }

    // Debounce.

    @Test
    public void secondCrashWithinTwoMinutesIsNotReported() {
        drive(10_000);
        accel(10_500, 45);
        assertNotNull(stopFrom(13_000, 10_000));
        // Moving again, then another hit a minute after the first.
        for (long t = 60_000; t <= 70_000; t += 1000) fix(t, 20);
        accel(70_500, 45);
        assertFalse(detector.isPending());
    }

    @Test
    public void crashAfterTwoMinutesIsReportedAgain() {
        drive(10_000);
        accel(10_500, 45);
        assertNotNull(stopFrom(13_000, 10_000));
        for (long t = 125_000; t <= 131_000; t += 1000) fix(t, 20);
        accel(131_000, 45);
        assertTrue(detector.isPending());
        assertNotNull(stopFrom(133_000, 10_000));
    }

    @Test
    public void discardedImpactDoesNotHoldBackARealOne() {
        drive(10_000);
        accel(10_500, 45);
        fix(11_000, 20);  // a pothole: dropped
        accel(11_500, 50);
        assertTrue(detector.isPending());
        assertNotNull(stopFrom(14_000, 10_000));
    }

    // GPS without speed.

    @Test
    public void fixWithoutSpeedNeitherArmsNorConfirms() {
        detector.setDriving(true);
        for (long t = 0; t <= 10_000; t += 1000) {
            assertNull(detector.onFix(T0 + t, Double.NaN, 5, LAT, LON));
        }
        accel(10_500, 60);
        assertFalse(detector.isPending());

        drive(20_000);
        accel(20_500, 60);
        assertTrue(detector.isPending());
        // Speedless fixes after the impact count for nothing: no stop is
        // seen, so the candidate runs out 20 s after the impact.
        for (long t = 21_000; t <= 40_000; t += 1000) {
            assertNull(detector.onFix(T0 + t, Double.NaN, 5, LAT, LON));
            assertTrue(detector.isPending());
        }
        assertNull(detector.onFix(T0 + 41_000, Double.NaN, 5, LAT, LON));
        assertFalse(detector.isPending());
        assertNull(stopFrom(42_000, 10_000));
    }

    @Test
    public void speedlessFixesInTheHoldDoNotCount() {
        drive(10_000);
        accel(10_500, 45);
        fix(12_000, 0.3);
        for (long t = 13_000; t <= 22_000; t += 1000) {
            assertNull(detector.onFix(T0 + t, Double.NaN, 5, LAT, LON));
        }
        // Ten seconds stopped, but only one fix with speed so far.
        assertTrue(detector.isPending());
        assertNotNull(fix(23_000, 0.3));
    }

    @Test
    public void coarseFixIsNotTrusted() {
        drive(10_000);
        accel(10_500, 45);
        detector.onFix(T0 + 11_000, 30, 80, LAT, LON);  // a network fix's wild speed
        assertTrue(detector.isPending());
    }

    @Test
    public void stopWithNoFixesAfterIsGivenUp() {
        drive(10_000);
        accel(10_500, 45);
        fix(12_000, 0.3);
        // Nothing for half a minute, then the accelerometer ticks on.
        accel(12_000 + CrashDetector.HOLD_MS + CrashDetector.HOLD_GRACE_MS + 1, 0);
        assertFalse(detector.isPending());
    }

    // A crash that takes the GPS with it.

    @Test
    public void aHardImpactWithNoFixAfterItIsStillACrash() {
        drive(10_000);
        accel(10_500, 45);  // about 4.6 g, and then the GPS goes quiet
        assertNull(detector.onAccel(T0 + 25_000, 0, 0, G));
        CrashDetector.Result r = detector.onAccel(T0 + 31_000, 0, 0, G);
        assertNotNull(r);
        assertEquals(4.6, r.peakG, 1e-9);
        assertEquals(T0 + 10_500, r.detectedAtMs);
        assertEquals(LAT, r.lat, 1e-9);  // the last place known before the impact
        assertFalse(detector.isPending());
    }

    @Test
    public void aSoftImpactWithNoFixAfterItIsNot() {
        drive(10_000);
        for (long t = 10_500; t <= 10_560; t += 20) accel(t, 27);  // ~2.7 g held: a jolt, not a 4 g hit
        assertTrue(detector.isPending());
        assertNull(detector.onAccel(T0 + 31_000, 0, 0, G));
        assertFalse(detector.isPending());
    }

    @Test
    public void aHardImpactWithTheCarDrivingOnIsNot() {
        drive(10_000);
        accel(10_500, 45);  // a pothole, or the phone dropped
        assertNull(fix(12_000, 20));
        assertNull(detector.onAccel(T0 + 31_000, 0, 0, G));
        assertFalse(detector.isPending());
    }
}
