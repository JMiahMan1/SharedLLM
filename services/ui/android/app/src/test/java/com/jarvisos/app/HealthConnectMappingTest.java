package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.time.OffsetDateTime;
import java.util.Arrays;
import java.util.Collections;
import org.json.JSONObject;
import org.junit.Test;

/** Health Connect record fields → the health hub's ingestion payloads. */
public class HealthConnectMappingTest {

    private static final int PHOENIX = -7 * 3600;
    private static final long MIN = 60_000L;

    private static long at(String iso) {
        return OffsetDateTime.parse(iso).toInstant().toEpochMilli();
    }

    @Test
    public void firstSyncReadsThirtyDaysThenOverlapsFortyEightHours() {
        long now = at("2026-10-24T12:00:00Z");
        assertEquals(now - HealthConnectMapping.FIRST_WINDOW_MS, HealthConnectMapping.windowStart(0, now));
        long last = at("2026-10-24T08:00:00Z");
        assertEquals(last - HealthConnectMapping.OVERLAP_MS, HealthConnectMapping.windowStart(last, now));
        // A cursor older than the history window never reaches past it.
        long ancient = now - 90L * 24 * 3600 * 1000;
        assertEquals(now - HealthConnectMapping.FIRST_WINDOW_MS, HealthConnectMapping.windowStart(ancient, now));
    }

    @Test
    public void sleepWithStagesCountsTimeAsleepAndEfficiency() throws Exception {
        long bed = at("2026-10-23T22:30:00-07:00");
        long wake = at("2026-10-24T06:30:00-07:00");
        JSONObject night = HealthConnectMapping.sleep("abc", bed, PHOENIX, wake, PHOENIX, Arrays.asList(
            new HealthConnectMapping.Stage(HealthConnectMapping.STAGE_AWAKE, bed, bed + 20 * MIN),
            new HealthConnectMapping.Stage(HealthConnectMapping.STAGE_LIGHT, bed + 20 * MIN, bed + 260 * MIN),
            new HealthConnectMapping.Stage(HealthConnectMapping.STAGE_DEEP, bed + 260 * MIN, bed + 350 * MIN),
            new HealthConnectMapping.Stage(HealthConnectMapping.STAGE_REM, bed + 350 * MIN, bed + 440 * MIN),
            new HealthConnectMapping.Stage(HealthConnectMapping.STAGE_AWAKE_IN_BED, bed + 440 * MIN, wake)));

        // The night is the day it ended, in the zone it was recorded in.
        assertEquals("2026-10-24", night.getString("night"));
        assertEquals("2026-10-23T22:30:00-07:00", night.getString("bedtime"));
        assertEquals("2026-10-24T06:30:00-07:00", night.getString("wake_time"));
        assertEquals(420, night.getInt("duration_minutes"));
        assertEquals(87.5, night.getDouble("efficiency"), 0.001);
        JSONObject stages = night.getJSONObject("stages");
        assertEquals(240, stages.getInt("light"));
        assertEquals(90, stages.getInt("deep"));
        assertEquals(90, stages.getInt("rem"));
        assertEquals(60, stages.getInt("awake"));
        assertEquals("health_connect", night.getString("source"));
        assertEquals("hc:sleep:abc", night.getString("external_id"));
    }

    @Test
    public void sleepWithoutStagesIsTheWholeSessionAndClaimsNoEfficiency() throws Exception {
        long bed = at("2026-10-23T23:00:00-07:00");
        JSONObject night = HealthConnectMapping.sleep("x", bed, PHOENIX, bed + 450 * MIN, PHOENIX,
            Collections.emptyList());
        assertEquals(450, night.getInt("duration_minutes"));
        assertFalse(night.has("efficiency"));
    }

    @Test
    public void stagesOutsideTheSessionAreClipped() throws Exception {
        long bed = at("2026-10-23T23:00:00Z");
        long wake = bed + 60 * MIN;
        JSONObject night = HealthConnectMapping.sleep("c", bed, 0, wake, 0, Collections.singletonList(
            new HealthConnectMapping.Stage(HealthConnectMapping.STAGE_LIGHT, bed - 30 * MIN, wake + 30 * MIN)));
        assertEquals(60, night.getInt("duration_minutes"));
        assertEquals(100.0, night.getDouble("efficiency"), 0.001);
    }

    @Test
    public void emptyOrDayLongSessionsAreNotNights() throws Exception {
        long t = at("2026-10-24T00:00:00Z");
        assertNull(HealthConnectMapping.sleep("z", t, 0, t, 0, null));
        assertNull(HealthConnectMapping.sleep("z", t, 0, t + 25L * 60 * MIN, 0, null));
    }

    @Test
    public void metricsCarryUnitsSourceAndAStableId() throws Exception {
        long t = at("2026-10-24T07:05:00-07:00");
        JSONObject rhr = HealthConnectMapping.metric("resting_hr", "resting_hr", "r1", 58, "bpm", t, PHOENIX,
            "com.fitbit.FitbitMobile");
        assertEquals("resting_hr", rhr.getString("metric"));
        assertEquals(58.0, rhr.getDouble("value"), 0.0);
        assertEquals("bpm", rhr.getString("unit"));
        assertEquals("2026-10-24T07:05:00-07:00", rhr.getString("recorded_at"));
        assertEquals("health_connect", rhr.getString("source"));
        assertEquals("hc:resting_hr:r1", rhr.getString("external_id"));
        assertEquals("com.fitbit.FitbitMobile", rhr.getJSONObject("metadata").getString("origin"));

        JSONObject weight = HealthConnectMapping.metric("weight", "weight", "w1", 81.234567, "kg", t, null, null);
        assertEquals(81.23, weight.getDouble("value"), 0.0);
        assertTrue(weight.getJSONObject("metadata").length() == 0);
    }

    @Test
    public void readingsOutsideTheStoreBoundsAreDroppedNotSent() throws Exception {
        long t = at("2026-10-24T07:00:00Z");
        assertNull(HealthConnectMapping.metric("resting_hr", "resting_hr", "a", 300, "bpm", t, 0, null));
        assertNull(HealthConnectMapping.metric("weight", "weight", "b", 0.2, "kg", t, 0, null));
        assertNull(HealthConnectMapping.metric("hrv", "hrv", "c", Double.NaN, "ms", t, 0, null));
        assertNull(HealthConnectMapping.metric("steps", "steps", "d", 1000, "count", t, 0, null));
    }

    @Test
    public void workoutsUseGeoNamesAndNeverCarrySteps() throws Exception {
        long start = at("2026-10-24T06:00:00Z");
        JSONObject walk = HealthConnectMapping.workout("e1", HealthConnectMapping.EXERCISE_HIKING, " Camelback ",
            start, start + 90 * MIN, 5200.0, "com.google.android.apps.fitness");
        assertEquals("hc:exercise:e1", walk.getString("external_id"));
        assertEquals("walking", walk.getString("activity_type"));
        assertEquals(start / 1000.0, walk.getDouble("start_time"), 0.0);
        assertEquals((start + 90 * MIN) / 1000.0, walk.getDouble("end_time"), 0.0);
        assertEquals(5200.0, walk.getDouble("distance_meters"), 0.0);
        assertEquals("Camelback", walk.getString("title"));
        assertFalse(walk.has("steps"));

        JSONObject treadmill = HealthConnectMapping.workout("e2", HealthConnectMapping.EXERCISE_RUNNING_TREADMILL,
            null, start, start + 30 * MIN, null, null);
        assertEquals("running", treadmill.getString("activity_type"));
        assertFalse(treadmill.has("distance_meters"));
        assertEquals("cycling", HealthConnectMapping.activityType(HealthConnectMapping.EXERCISE_BIKING_STATIONARY));
    }

    @Test
    public void exerciseGeoCannotNameIsNotSent() throws Exception {
        long start = at("2026-10-24T06:00:00Z");
        int swimmingPool = 74;
        assertNull(HealthConnectMapping.workout("s", swimmingPool, null, start, start + 30 * MIN, null, null));
        assertNull(HealthConnectMapping.workout("b", HealthConnectMapping.EXERCISE_WALKING, null, start, start, null, null));
    }
}
