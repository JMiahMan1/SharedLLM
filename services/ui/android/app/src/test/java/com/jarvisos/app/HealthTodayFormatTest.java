package com.jarvisos.app;

import static org.junit.Assert.assertEquals;

import com.jarvisos.app.widgets.HealthTodayFormat;
import org.json.JSONObject;
import org.junit.Test;

/** The Health today widget never prints a reading that was not taken. */
public class HealthTodayFormatTest {

    private static JSONObject json(String text) throws Exception {
        return new JSONObject(text);
    }

    @Test
    public void stepsShowTodayAgainstTheGoalOrSayWhyNot() throws Exception {
        HealthTodayFormat.Tile t = HealthTodayFormat.steps(
            json("{\"today\": 6421, \"goal\": 8000, \"daily_steps\": {\"2026-10-24\": 6421}}"));
        assertEquals("6,421", t.value);
        assertEquals("of 8,000", t.caption);

        assertEquals("No steps yet", HealthTodayFormat.steps(json("{\"today\": 0, \"daily_steps\": {}}")).caption);
        assertEquals(HealthTodayFormat.DASH, HealthTodayFormat.steps(null).value);
        assertEquals("Unavailable", HealthTodayFormat.steps(null).caption);
        assertEquals("steps", HealthTodayFormat.steps(
            json("{\"today\": 10, \"goal\": 0, \"daily_steps\": {\"d\": 10}}")).caption);
    }

    @Test
    public void readinessUsesTheServersLabelOrAsksForACheckIn() throws Exception {
        HealthTodayFormat.Tile t = HealthTodayFormat.readiness(json("{\"readiness\": {\"score\": 74, \"label\": \"Good\"}}"));
        assertEquals("74", t.value);
        assertEquals("Good", t.caption);
        HealthTodayFormat.Tile none = HealthTodayFormat.readiness(json("{\"readiness\": null}"));
        assertEquals(HealthTodayFormat.DASH, none.value);
        assertEquals("Check in", none.caption);
    }

    @Test
    public void sleepShowsScoreAndLength() throws Exception {
        HealthTodayFormat.Tile t = HealthTodayFormat.sleep(json(
            "{\"sleep\": {\"score\": {\"score\": 82, \"label\": \"Good\"}, \"last_night\": {\"duration_minutes\": 425}}}"));
        assertEquals("82", t.value);
        assertEquals("7h 05m", t.caption);
        HealthTodayFormat.Tile none = HealthTodayFormat.sleep(json("{\"sleep\": {\"score\": null, \"last_night\": null}}"));
        assertEquals(HealthTodayFormat.DASH, none.value);
        assertEquals("Not logged", none.caption);
        assertEquals("45m", HealthTodayFormat.hoursMinutes(45));
    }

    @Test
    public void waterFollowsTheProfileUnitsAndNeverPrintsAFakeZero() throws Exception {
        HealthTodayFormat.Tile metric = HealthTodayFormat.water(json(
            "{\"profile\": {\"units\": \"metric\"}, \"water\": {\"ml\": 1250, \"goal_ml\": 2000, \"logged\": true}}"));
        assertEquals("1.3 L", metric.value);
        assertEquals("of 2.0 L", metric.caption);

        HealthTodayFormat.Tile imperial = HealthTodayFormat.water(json(
            "{\"profile\": {\"units\": \"imperial\"}, \"water\": {\"ml\": 1183, \"goal_ml\": 2000, \"logged\": true}}"));
        assertEquals("40 oz", imperial.value);
        assertEquals("of 68 oz", imperial.caption);

        HealthTodayFormat.Tile none = HealthTodayFormat.water(json(
            "{\"water\": {\"ml\": 0, \"goal_ml\": 2000, \"logged\": false}}"));
        assertEquals(HealthTodayFormat.DASH, none.value);
        assertEquals("None logged", none.caption);
    }
}
