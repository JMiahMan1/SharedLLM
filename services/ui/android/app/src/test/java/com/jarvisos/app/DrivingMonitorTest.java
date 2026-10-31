package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;

import org.json.JSONObject;
import org.junit.Test;

public class DrivingMonitorTest {
    private static final long T0 = 1_760_000_000_000L;
    private static final double LAT = 35.0;
    private static final double LON = -80.0;

    private final DrivingMonitor monitor = new DrivingMonitor();

    private List<DrivingMonitor.Event> fix(long t, double speed, double acc, boolean screen) {
        return monitor.onFix(T0 + t, speed, acc, LAT, LON, screen);
    }

    private List<DrivingMonitor.Event> fix(long t, double speed) {
        return fix(t, speed, 5, false);
    }

    private static List<String> types(List<DrivingMonitor.Event> events) {
        List<String> out = new ArrayList<>();
        for (DrivingMonitor.Event e : events) out.add(e.type);
        return out;
    }

    // Hard braking.

    @Test
    public void brakeAtThresholdIsReported() {
        fix(0, 20);
        List<DrivingMonitor.Event> events = fix(1000, 16.6);
        assertEquals(List.of("hard_brake"), types(events));
        DrivingMonitor.Event e = events.get(0);
        assertEquals(T0 + 1000, e.atMs);
        assertEquals(20, e.fromMps, 1e-9);
        assertEquals(16.6, e.toMps, 1e-9);
    }

    @Test
    public void gentleBrakeIsNot() {
        fix(0, 20);
        assertTrue(fix(1000, 17).isEmpty());
    }

    @Test
    public void brakeFromACrawlIsNot() {
        fix(0, 6.9);
        assertTrue(fix(1000, 0).isEmpty());
    }

    @Test
    public void brakeAcrossAGapIsNot() {
        fix(0, 25);
        assertTrue(fix(3_500, 10).isEmpty());
    }

    @Test
    public void fixesTooCloseTogetherAreNotCompared() {
        fix(0, 25);
        assertTrue(fix(400, 20).isEmpty());
    }

    @Test
    public void imprecisePairIsNotCompared() {
        fix(0, 25, 25, false);
        assertTrue(fix(1000, 15, 5, false).isEmpty());
        fix(2000, 25, 5, false);
        assertTrue(fix(3000, 15, 21, false).isEmpty());
    }

    @Test
    public void fixWithoutSpeedIsSkipped() {
        fix(0, 25);
        assertTrue(fix(500, Double.NaN).isEmpty());
        // Compared with the last fix that had a speed.
        assertEquals(List.of("hard_brake"), types(fix(1500, 20)));
    }

    @Test
    public void oneBrakePerTenSeconds() {
        fix(0, 30);
        assertEquals(1, fix(1000, 25).size());
        assertTrue(fix(2000, 20).isEmpty());
        assertTrue(fix(9000, 25).isEmpty());
        assertTrue(fix(10_000, 20).isEmpty());
        assertEquals(List.of("hard_brake"), types(fix(11_000, 15)));
    }

    // Rapid acceleration.

    @Test
    public void rapidAccelIsReported() {
        fix(0, 2);
        List<DrivingMonitor.Event> events = fix(1000, 6);
        assertEquals(List.of("rapid_accel"), types(events));
        assertEquals(2, events.get(0).fromMps, 1e-9);
    }

    @Test
    public void accelEndingSlowIsNot() {
        fix(0, 0);
        assertTrue(fix(1000, 3.5).isEmpty());
    }

    @Test
    public void oneAccelPerTenSeconds() {
        fix(0, 0);
        assertEquals(1, fix(1000, 5).size());
        assertTrue(fix(2000, 10).isEmpty());
        fix(5000, 10);
        assertTrue(fix(6000, 14).isEmpty());
        fix(10_500, 14);
        assertEquals(List.of("rapid_accel"), types(fix(11_500, 18)));
    }

    // High speed.

    @Test
    public void highSpeedNeedsTenSeconds() {
        for (int s = 0; s < 10; s++) assertTrue(fix(s * 1000L, 36).isEmpty());
        List<DrivingMonitor.Event> events = fix(10_000, 36);
        assertEquals(List.of("high_speed"), types(events));
        assertEquals(80.5, DrivingMonitor.mph(events.get(0).speedMps), 1e-9);
    }

    @Test
    public void highSpeedOncePerEpisodeAndRearmedBelow75() {
        for (int s = 0; s <= 10; s++) fix(s * 1000L, 36);
        // Dipping to 77 mph and back is the same episode.
        for (int s = 11; s <= 30; s++) assertTrue(fix(s * 1000L, s == 15 ? 34.5 : 36).isEmpty());
        // Below 75 mph re-arms; another ten seconds above 80 is a new episode.
        // (Fixes 4 s apart around the dip, so it is not also a brake and an acceleration.)
        fix(34_000, 30);
        List<String> seen = new ArrayList<>();
        for (int s = 38; s <= 48; s++) seen.addAll(types(fix(s * 1000L, 36)));
        assertEquals(List.of("high_speed"), seen);
    }

    @Test
    public void dropBelow80RestartsTheCount() {
        for (int s = 0; s < 8; s++) fix(s * 1000L, 36);
        fix(8_000, 35);
        for (int s = 9; s < 19; s++) assertTrue(fix(s * 1000L, 36).isEmpty());
        assertEquals(List.of("high_speed"), types(fix(19_000, 36)));
    }

    @Test
    public void impreciseSpeedDoesNotCount() {
        for (int s = 0; s <= 12; s++) assertTrue(fix(s * 1000L, 40, 50, false).isEmpty());
    }

    // Phone use.

    @Test
    public void phoneUseLastingFifteenSecondsIsReportedWhenItEnds() {
        for (int s = 0; s <= 20; s++) assertTrue(fix(s * 1000L, 15, 5, true).isEmpty());
        List<DrivingMonitor.Event> events = fix(21_000, 15, 5, false);
        assertEquals(List.of("phone_use"), types(events));
        DrivingMonitor.Event e = events.get(0);
        assertEquals(T0, e.atMs);
        assertEquals(20_000, e.durationMs);
    }

    @Test
    public void shortPhoneUseIsNot() {
        for (int s = 0; s <= 10; s++) fix(s * 1000L, 15, 5, true);
        assertTrue(fix(11_000, 15, 5, false).isEmpty());
    }

    @Test
    public void phoneUseWhileStoppedIsNot() {
        for (int s = 0; s <= 30; s++) assertTrue(fix(s * 1000L, 2, 5, true).isEmpty());
        assertTrue(monitor.finish().isEmpty());
    }

    @Test
    public void stoppingEndsTheStretch() {
        for (int s = 0; s <= 16; s++) fix(s * 1000L, 15, 5, true);
        // More than 3 s on, so the slowdown is not also judged a hard brake.
        assertEquals(List.of("phone_use"), types(fix(19_500, 1, 5, true)));
    }

    @Test
    public void openStretchIsReportedWhenDrivingEnds() {
        for (int s = 0; s <= 16; s++) fix(s * 1000L, 15, 5, true);
        List<DrivingMonitor.Event> events = monitor.finish();
        assertEquals(List.of("phone_use"), types(events));
        assertEquals(16_000, events.get(0).durationMs);
        assertTrue(monitor.finish().isEmpty());
    }

    @Test
    public void aGapEndsTheStretch() {
        for (int s = 0; s <= 16; s++) fix(s * 1000L, 15, 5, true);
        List<DrivingMonitor.Event> events = fix(40_000, 15, 5, true);
        assertEquals(List.of("phone_use"), types(events));
        assertEquals(16_000, events.get(0).durationMs);
    }

    @Test
    public void fixWithoutSpeedCarriesButCannotStart() {
        for (int s = 0; s <= 10; s++) assertTrue(fix(s * 1000L, Double.NaN, 5, true).isEmpty());
        assertTrue(monitor.finish().isEmpty());
        fix(20_000, 15, 5, true);
        for (int s = 21; s <= 36; s++) fix(s * 1000L, Double.NaN, 30, true);
        assertEquals(List.of("phone_use"), types(monitor.finish()));
    }

    // The posted body.

    @Test
    public void bodyHasTheActivityShape() throws Exception {
        fix(0, 20);
        JSONObject body = fix(1000, 10).get(0).toBody();
        assertEquals("driving", body.getString("feature"));
        assertEquals("hard_brake", body.getString("event"));
        assertEquals("", body.getString("item"));
        JSONObject detail = body.getJSONObject("detail");
        assertEquals(T0 + 1000, detail.getLong("at"));
        assertEquals(LAT, detail.getDouble("lat"), 1e-9);
        assertEquals(LON, detail.getDouble("lon"), 1e-9);
        assertEquals(22.4, detail.getDouble("speed_mph"), 1e-9);
        assertEquals(44.7, detail.getDouble("from_mph"), 1e-9);
        assertEquals(22.4, detail.getDouble("to_mph"), 1e-9);
        assertFalse(detail.has("duration_s"));
    }

    @Test
    public void phoneUseBodyHasDurationAndNoSpeedChange() throws Exception {
        for (int s = 0; s <= 20; s++) fix(s * 1000L, s == 5 ? 25 : 15, 5, true);
        JSONObject detail = monitor.finish().get(0).toBody().getJSONObject("detail");
        assertEquals(20, detail.getLong("duration_s"));
        assertEquals(55.9, detail.getDouble("speed_mph"), 1e-9);
        assertFalse(detail.has("from_mph"));
        assertFalse(detail.has("to_mph"));
    }
}
