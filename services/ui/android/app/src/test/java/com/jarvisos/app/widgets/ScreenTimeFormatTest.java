package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;

import java.util.TimeZone;
import org.json.JSONObject;
import org.junit.Test;

/** Desktop-JVM tests for the screen-time widget's words. */
public class ScreenTimeFormatTest {
    private static final TimeZone DENVER = TimeZone.getTimeZone("America/Denver");

    @Test
    public void aChildWithTimeLeftSeesItAndWhatLocksNext() throws Exception {
        JSONObject s = new JSONObject().put("locked", false).put("limit_min", 120).put("remaining_min", 80)
            .put("used_min", 40).put("next_lock_at", "2026-11-08T21:15:00").put("next_lock_reason", "bedtime");
        assertEquals("1h 20m left today", ScreenTimeFormat.headline(s));
        assertEquals("1h 20m", ScreenTimeFormat.left(s));
        assertEquals(67, ScreenTimeFormat.percentLeft(s));
        assertEquals("Bedtime at 9:15 PM", ScreenTimeFormat.line1(s, DENVER));
        assertEquals("Used 40m today", ScreenTimeFormat.line2(s));
    }

    @Test
    public void aLockedPhoneSaysWhyAndUntilWhen() throws Exception {
        JSONObject s = new JSONObject().put("locked", true).put("reason", "bedtime").put("until", "2026-11-09T07:00:00")
            .put("limit_min", 120).put("remaining_min", 30);
        assertEquals("It's bedtime", ScreenTimeFormat.headline(s));
        assertEquals("Until 7:00 AM", ScreenTimeFormat.line1(s, DENVER));
        assertEquals("Locked", ScreenTimeFormat.value(s));
        assertEquals(0, ScreenTimeFormat.percentLeft(s));
    }

    @Test
    public void aParentPauseReadsAsPaused() throws Exception {
        JSONObject s = new JSONObject().put("locked", true).put("reason", "paused");
        assertEquals("Paused", ScreenTimeFormat.value(s));
        assertEquals("Until a parent unlocks it", ScreenTimeFormat.line1(s, DENVER));
    }

    @Test
    public void noLimitAndChores() throws Exception {
        JSONObject s = new JSONObject().put("locked", false).put("limit_min", JSONObject.NULL).put("chores_left", 2);
        assertEquals("No time limit today", ScreenTimeFormat.headline(s));
        assertEquals("2 chores to earn more", ScreenTimeFormat.line2(s));
    }
}
