package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

/** Desktop-JVM tests for the Bible hub's Continue and Plan tiles. */
public class BibleHubTest {

    @Test
    public void continuesWhereTheReaderStopped() throws Exception {
        JSONObject state = new JSONObject().put("position", new JSONObject()
            .put("book", "John").put("chapter", 3).put("verse", 16).put("label", "John 3"));
        BibleHub.Continue c = BibleHub.continueReading(state);
        assertEquals("John 3", c.value);
        assertEquals("John 3:16", c.ref);
    }

    @Test
    public void aNewReaderIsInvitedToStart() throws Exception {
        BibleHub.Continue c = BibleHub.continueReading(new JSONObject().put("position", JSONObject.NULL));
        assertEquals("Start reading", c.value);
        assertNull(c.ref);
    }

    @Test
    public void todaysPlanNamesTheReadingAndTheDay() throws Exception {
        JSONObject plans = new JSONObject().put("mine", new JSONArray().put(new JSONObject()
            .put("name", "Psalms in a Month").put("days", 30).put("behind", 2)
            .put("today", new JSONObject().put("day", 12).put("readings", new JSONArray()
                .put(new JSONObject().put("ref", "Psalm 23"))
                .put(new JSONObject().put("ref", "Psalm 24"))))));
        BibleHub.Plan p = BibleHub.todaysPlan(plans);
        assertEquals("Psalm 23 +1", p.value);
        assertEquals("Day 12 of 30 · 2 behind", p.caption);
        assertEquals("Psalm 23", p.ref);
    }

    @Test
    public void noPlanOffersTheCatalogue() throws Exception {
        assertEquals("Choose a plan", BibleHub.todaysPlan(new JSONObject().put("mine", new JSONArray())).value);
        assertEquals("Choose a plan", BibleHub.todaysPlan(null).value);
    }
}
