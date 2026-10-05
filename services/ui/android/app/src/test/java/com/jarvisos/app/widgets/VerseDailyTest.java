package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

/** Desktop-JVM tests for what the verse widget prints and why. */
public class VerseDailyTest {

    private static JSONObject daily(String verseJson, String devotionalJson) throws Exception {
        return new JSONObject()
            .put("day", "2026-10-03")
            .put("verse_of_day", new JSONObject(verseJson))
            .put("devotional", new JSONObject(devotionalJson))
            .put("streaks", new JSONObject()
                .put("read_streak_current", 4)
                .put("read_streak_longest", 11)
                .put("days_read", 30)
                .put("chapters_read", 122));
    }

    private static final String GOOD_VERSE =
        "{\"reference\":\"John 3:16\",\"text\":\"For God so loved the world.\",\"version\":\"kjv\"}";

    @Test
    public void printsTheVerseAndItsReference() throws Exception {
        VerseDaily.Lines lines = VerseDaily.fromJson(daily(GOOD_VERSE, "{\"entry\":null,\"reason\":\"\"}"));
        assertEquals("John 3:16", lines.reference);
        assertEquals("For God so loved the world.", lines.text);
        assertEquals("", lines.warning);
    }

    @Test
    public void prefersTheDevotionalsOwnScriptureWhenItHasOne() throws Exception {
        String devotional = "{\"entry\":{\"reference\":\"Psalm 46:1\",\"title\":\"God is our refuge\"},\"reason\":\"\"}";
        VerseDaily.Lines lines = VerseDaily.fromJson(daily(GOOD_VERSE, devotional));
        assertEquals("Devotional: Psalm 46:1", lines.sub);
    }

    @Test
    public void fallsBackToTheDevotionalTitleWhenThereIsNoReading() throws Exception {
        String devotional = "{\"entry\":{\"title\":\"Day by Day by Grace\"},\"reason\":\"\"}";
        VerseDaily.Lines lines = VerseDaily.fromJson(daily(GOOD_VERSE, devotional));
        assertEquals("Day by Day by Grace", lines.sub);
    }

    @Test
    public void aDevotionalThatIsNotConfiguredSaysSoRatherThanGoingBlank() throws Exception {
        String devotional = "{\"entry\":null,\"reason\":\"No devotional source had an entry for 2026-10-03.\"}";
        VerseDaily.Lines lines = VerseDaily.fromJson(daily(GOOD_VERSE, devotional));
        assertEquals("No devotional source had an entry for 2026-10-03.", lines.sub);
    }

    @Test
    public void aVerseLevelErrorIsShownInsteadOfAnEmptyCard() throws Exception {
        String verse = "{\"error\":\"No verses are loaded for scope \\\"ot\\\". Import a translation.\"}";
        VerseDaily.Lines lines = VerseDaily.fromJson(daily(verse, "{\"entry\":null,\"reason\":\"\"}"));
        assertEquals("—", lines.text);
        assertTrue(lines.sub.contains("Import a translation."));
        assertTrue(lines.warning.contains("Import a translation."));
    }

    @Test
    public void aMissingVerseObjectIsNotACrash() throws Exception {
        JSONObject payload = new JSONObject().put("day", "2026-10-03")
            .put("devotional", new JSONObject().put("entry", JSONObject.NULL));
        VerseDaily.Lines lines = VerseDaily.fromJson(payload);
        assertEquals("—", lines.text);
        assertEquals("No verse available today", lines.sub);
    }

    @Test
    public void anEmptyPayloadIsStillAReadableCard() {
        VerseDaily.Lines lines = VerseDaily.fromJson(null);
        assertEquals("—", lines.text);
        assertEquals("No verse available today", lines.sub);
    }

    @Test
    public void theStreakIsOnlyClaimedWhenThereIsOne() throws Exception {
        VerseDaily.Lines withStreak = VerseDaily.fromJson(daily(GOOD_VERSE, "{\"entry\":null,\"reason\":\"\"}"));
        assertEquals("4 day streak", withStreak.streak);

        JSONObject payload = daily(GOOD_VERSE, "{\"entry\":null,\"reason\":\"\"}");
        payload.put("streaks", new JSONObject().put("read_streak_current", 0));
        assertEquals("Start a streak today", VerseDaily.fromJson(payload).streak);

        payload.remove("streaks");
        assertEquals("", VerseDaily.fromJson(payload).streak);
    }

    @Test
    public void theTranslationIsShownWhenThereIsNothingElseToSay() throws Exception {
        VerseDaily.Lines lines = VerseDaily.fromJson(daily(GOOD_VERSE, "{\"entry\":null}"));
        assertEquals("kjv", lines.sub);
    }

    @Test
    public void longServerMessagesCollapseToOneLine() {
        String long40 = "0123456789012345678901234567890123456789";
        assertEquals(80, VerseDaily.oneLine(long40).length());
        assertEquals("a b", VerseDaily.oneLine("a\n  b"));
        assertEquals("offline", VerseDaily.oneLine(""));
        assertEquals("offline", VerseDaily.oneLine(null));
    }
}