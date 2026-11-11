package com.jarvisos.app;

import static org.junit.Assert.assertEquals;

import org.json.JSONObject;
import org.junit.Test;

/** Desktop-JVM tests for how today's screen time reads on the tile and the time panel. */
public class TimeUsageTest {

    @Test
    public void usedCountsAgainstTheLimitPlusEverythingAddedToIt() throws Exception {
        JSONObject s = new JSONObject().put("limit_min", 120).put("bonus_min", 15).put("earned_min", 20)
                .put("banked_min", 5).put("used_min", 80).put("remaining_min", 80);
        assertEquals(160, TimeUsage.allowed(s));
        assertEquals(50, TimeUsage.usedPercent(s));
        assertEquals("Includes 20m earned, 15m from a parent, 5m saved from earlier", TimeUsage.extraLine(s));
        assertEquals("1h 20m left", TimeUsage.tileLine(s));
    }

    @Test
    public void aPlainLimitHasNoExtraLine() throws Exception {
        JSONObject s = new JSONObject().put("limit_min", 60).put("used_min", 75).put("remaining_min", 0);
        assertEquals("", TimeUsage.extraLine(s));
        assertEquals(100, TimeUsage.usedPercent(s));
    }

    @Test
    public void noLimitShowsTimeUsed() throws Exception {
        JSONObject s = new JSONObject().put("limit_min", JSONObject.NULL).put("used_min", 95);
        assertEquals("Used 1h 35m", TimeUsage.tileLine(s));
        assertEquals("", TimeUsage.extraLine(s));
    }

    @Test
    public void aLockedPhoneSaysSoAndAnUnknownOneSaysNotAvailable() throws Exception {
        assertEquals("Paused", TimeUsage.tileLine(new JSONObject().put("locked", true).put("reason", "paused")));
        assertEquals("Locked", TimeUsage.tileLine(new JSONObject().put("locked", true).put("reason", "bedtime")));
        assertEquals("Not available", TimeUsage.tileLine(new JSONObject()));
    }
}
