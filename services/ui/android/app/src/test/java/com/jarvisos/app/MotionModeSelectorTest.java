package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import com.jarvisos.app.MotionModeSelector.Mode;

import org.json.JSONObject;
import org.junit.Test;

public class MotionModeSelectorTest {
    private static final long T0 = 1_760_000_000_000L;

    private static MotionModeSelector unknown() {
        return new MotionModeSelector(Mode.UNKNOWN, 0L);
    }

    // Speed alone (no Activity Recognition).

    @Test
    public void gpsFixAtSevenMetresASecondStartsADrive() {
        MotionModeSelector m = unknown();
        assertFalse(m.onFix(T0, 6.9, 5));
        assertEquals(Mode.UNKNOWN, m.mode());
        assertTrue(m.onFix(T0 + 1000, 7.0, 5));
        assertEquals(Mode.DRIVING, m.mode());
    }

    @Test
    public void networkFixCannotStartADrive() {
        MotionModeSelector m = unknown();
        // No speed, or a coarse fix whose speed is not trusted.
        assertFalse(m.onFix(T0, Double.NaN, 80));
        assertFalse(m.onFix(T0, 15, 80));
        assertFalse(m.onFix(T0, 15, Double.NaN));
        assertEquals(Mode.UNKNOWN, m.mode());
    }

    @Test
    public void threeSlowMinutesEndADrive() {
        MotionModeSelector m = unknown();
        m.onFix(T0, 15, 5);
        // A red light: slow, but under three minutes.
        for (long t = 1000; t < 180_000; t += 10_000) assertFalse(m.onFix(T0 + t, 0.5, 5));
        assertEquals(Mode.DRIVING, m.mode());
        assertTrue(m.onFix(T0 + 180_000, 0.5, 5));
        assertEquals(Mode.UNKNOWN, m.mode());
    }

    @Test
    public void movingAgainKeepsTheDrive() {
        MotionModeSelector m = unknown();
        m.onFix(T0, 15, 5);
        m.onFix(T0 + 120_000, 2.0, 5);
        assertFalse(m.onFix(T0 + 250_000, 0, 5));
        assertEquals(Mode.DRIVING, m.mode());
        assertTrue(m.onFix(T0 + 300_000, 0, 5));
    }

    @Test
    public void fixesWithoutSpeedDoNotExtendADrive() {
        MotionModeSelector m = unknown();
        m.onFix(T0, 15, 5);
        assertTrue(m.onFix(T0 + 180_000, Double.NaN, 40));
        assertEquals(Mode.UNKNOWN, m.mode());
    }

    @Test
    public void heartbeatEndsASilentDrive() {
        MotionModeSelector m = unknown();
        m.onFix(T0, 15, 5);
        assertFalse(m.tick(T0 + 179_000));
        assertTrue(m.tick(T0 + 180_000));
        assertEquals(Mode.UNKNOWN, m.mode());
    }

    @Test
    public void restoredDriveFromLongAgoEndsAtOnce() {
        MotionModeSelector m = new MotionModeSelector(Mode.DRIVING, T0);
        assertTrue(m.tick(T0 + 3_600_000));
        assertEquals(Mode.UNKNOWN, m.mode());
    }

    @Test
    public void restoredDriveCarriesOn() {
        MotionModeSelector m = new MotionModeSelector(Mode.DRIVING, T0);
        assertFalse(m.onFix(T0 + 60_000, 20, 5));
        assertEquals(Mode.DRIVING, m.mode());
        assertEquals(T0 + 60_000, m.lastMovingAt());
    }

    // Activity Recognition.

    @Test
    public void transitionsSetTheMode() {
        MotionModeSelector m = unknown();
        assertTrue(m.onTransition(Mode.STILL, true, T0));
        assertEquals(Mode.STILL, m.mode());
        assertTrue(m.onTransition(Mode.WALKING, true, T0));
        assertTrue(m.onTransition(Mode.CYCLING, true, T0));
        assertTrue(m.onTransition(Mode.DRIVING, true, T0));
        assertEquals(Mode.DRIVING, m.mode());
    }

    @Test
    public void exitOfTheCurrentModeFallsBackToUnknown() {
        MotionModeSelector m = unknown();
        m.onTransition(Mode.WALKING, true, T0);
        assertFalse(m.onTransition(Mode.STILL, false, T0));
        assertEquals(Mode.WALKING, m.mode());
        assertTrue(m.onTransition(Mode.WALKING, false, T0));
        assertEquals(Mode.UNKNOWN, m.mode());
    }

    @Test
    public void enteringAVehicleStartsTheSlowClockThen() {
        MotionModeSelector m = unknown();
        m.onTransition(Mode.DRIVING, true, T0);
        // Pulling out of a driveway slowly is not the end of the drive.
        assertFalse(m.onFix(T0 + 120_000, 1.0, 5));
        assertEquals(Mode.DRIVING, m.mode());
        assertTrue(m.tick(T0 + 180_000));
    }

    @Test
    public void speedOverridesStillAndWalkingButNotCycling() {
        MotionModeSelector still = unknown();
        still.onTransition(Mode.STILL, true, T0);
        assertTrue(still.onFix(T0, 12, 5));
        assertEquals(Mode.DRIVING, still.mode());

        MotionModeSelector bike = unknown();
        bike.onTransition(Mode.CYCLING, true, T0);
        assertFalse(bike.onFix(T0, 12, 5));
        assertEquals(Mode.CYCLING, bike.mode());
    }

    @Test
    public void losingActivityRecognitionDropsItsModes() {
        for (Mode mode : new Mode[]{Mode.STILL, Mode.WALKING, Mode.CYCLING}) {
            MotionModeSelector m = new MotionModeSelector(mode, 0L);
            assertTrue(m.dropActivityRecognition());
            assertEquals(Mode.UNKNOWN, m.mode());
        }
        MotionModeSelector driving = new MotionModeSelector(Mode.DRIVING, T0);
        assertFalse(driving.dropActivityRecognition());
        assertEquals(Mode.DRIVING, driving.mode());
    }

    @Test
    public void persistedNamesParse() {
        assertEquals(Mode.DRIVING, Mode.parse("DRIVING"));
        assertEquals(Mode.UNKNOWN, Mode.parse(null));
        assertEquals(Mode.UNKNOWN, Mode.parse("FLYING"));
        assertEquals(Mode.UNKNOWN, new MotionModeSelector(null, 0L).mode());
    }

    @Test
    public void ratesPerMode() {
        assertEquals(1_000L, Mode.DRIVING.intervalMs);
        assertEquals(0f, Mode.DRIVING.minDistanceM, 0f);
        assertEquals(15_000L, Mode.DRIVING.uploadEveryMs);
        assertTrue(Mode.DRIVING.highAccuracy);
        assertEquals(15_000L, Mode.WALKING.intervalMs);
        assertEquals(15_000L, Mode.CYCLING.intervalMs);
        assertTrue(Mode.WALKING.highAccuracy);
        assertFalse(Mode.STILL.highAccuracy);
        assertEquals(60_000L, Mode.STILL.intervalMs);
        // Still never uploads more often than before modes (30 s at most).
        assertTrue(Mode.STILL.uploadEveryMs >= 30_000L);
        assertEquals("driving", Mode.DRIVING.activity);
        assertEquals("cycling", Mode.CYCLING.activity);
        assertEquals("walking", Mode.WALKING.activity);
        assertEquals("still", Mode.STILL.activity);
        assertEquals("unknown", Mode.UNKNOWN.activity);
    }

    // The uploaded fix.

    @Test
    public void fixWithoutSpeedOmitsIt() throws Exception {
        JSONObject body = LocationTrackingService.fixFields(35.0, -80.0, 80, Double.NaN, Double.NaN,
                T0, T0 + 5_000, "unknown");
        assertFalse(body.has("speed"));
        assertFalse(body.has("bearing"));
        assertEquals("unknown", body.getString("activity"));
        assertEquals(80, body.getDouble("accuracy"), 1e-9);
        assertEquals(T0 / 1000.0, body.getDouble("timestamp"), 1e-6);
    }

    @Test
    public void fixWithSpeedSendsIt() throws Exception {
        JSONObject body = LocationTrackingService.fixFields(35.0, -80.0, 4, 16.1, 90, T0, T0, "driving");
        assertEquals(16.1, body.getDouble("speed"), 1e-9);
        assertEquals(90, body.getDouble("bearing"), 1e-9);
        assertEquals("driving", body.getString("activity"));
        assertEquals(35.0, body.getDouble("latitude"), 1e-9);
        assertEquals(-80.0, body.getDouble("longitude"), 1e-9);
    }

    @Test
    public void zeroSpeedIsStillSent() throws Exception {
        // A GPS fix that says 0 means stopped, which is worth sending.
        JSONObject body = LocationTrackingService.fixFields(35.0, -80.0, 4, 0, Double.NaN, T0, T0, "still");
        assertTrue(body.has("speed"));
        assertEquals(0, body.getDouble("speed"), 1e-9);
    }

    @Test
    public void fixWithoutTimeUsesNow() throws Exception {
        JSONObject body = LocationTrackingService.fixFields(35.0, -80.0, Double.NaN, Double.NaN, Double.NaN,
                0, T0, null);
        assertEquals(T0 / 1000.0, body.getDouble("timestamp"), 1e-6);
        assertEquals(0, body.getDouble("accuracy"), 1e-9);
        assertFalse(body.has("activity"));
    }
}
