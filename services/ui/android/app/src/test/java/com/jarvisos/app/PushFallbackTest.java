package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * The long poll the push service reads while the stream is unavailable
 * (docs/PUSH.md): when it takes over, how its cursor compares with the
 * outbox's timestamps, how its answers are read, and how the stream's replay
 * afterwards skips what it already showed.
 */
public class PushFallbackTest {

    @Test
    public void onlyAnUnavailableServerStartsThePoll() {
        assertTrue(PushFallback.streamUnavailable(503));
        assertTrue(PushFallback.streamUnavailable(502));
        assertTrue(PushFallback.streamUnavailable(504));
        assertFalse(PushFallback.streamUnavailable(200));
        assertFalse(PushFallback.streamUnavailable(401));
        assertFalse(PushFallback.streamUnavailable(422));
        assertFalse(PushFallback.streamUnavailable(500));
    }

    @Test
    public void theCursorHasTheOutboxTimestampForm() {
        // 2026-10-15T12:34:56.789Z
        assertEquals("2026-10-15T12:34:56.789000+00:00", PushFallback.outboxCursor(1792067696789L));
        assertEquals("1970-01-01T00:00:00.000000+00:00", PushFallback.outboxCursor(0L));
    }

    @Test
    public void theCursorOrdersAsTextLikeTheGatewayCompares() {
        String cursor = PushFallback.outboxCursor(1792067696789L);
        // Telemetry's stamps, as Python writes them, a moment before and after.
        assertTrue("2026-10-15T12:34:56.788999+00:00".compareTo(cursor) < 0);
        assertTrue("2026-10-15T12:34:56.789001+00:00".compareTo(cursor) > 0);
        assertTrue("2026-10-15T12:34:57+00:00".compareTo(cursor) > 0);
        assertTrue("2026-10-15T12:34:56+00:00".compareTo(cursor) < 0);
    }

    @Test
    public void answersAreReadAsNoticesWithTheNextCursor() throws Exception {
        PushFallback.Answer a = PushFallback.parse("{\"notifications\":["
            + "{\"id\":\"n1\",\"user\":\"ana\",\"category\":\"timers\",\"kind\":\"timer\",\"title\":\"Pasta\","
            + "\"body\":\"Done\",\"priority\":4,\"tag\":\"timer:pasta\",\"data\":{\"url\":\"/timers\"},"
            + "\"actions\":[{\"label\":\"Stop\",\"type\":\"callback\",\"callback\":\"timer_stop\"}],"
            + "\"created_at\":\"2026-10-15T12:00:00.000001+00:00\",\"read\":false},"
            + "{\"id\":\"n2\",\"title\":\"\",\"body\":\"\"},"
            + "\"not a notice\"],"
            + "\"cursor\":\"2026-10-15T12:00:00.000001+00:00\"}", "2026-10-15T11:00:00+00:00");
        assertEquals(1, a.notices.size());
        PushNotice n = a.notices.get(0);
        assertEquals("n1", n.id);
        assertEquals("timers", n.category);
        assertEquals("/timers", n.path);
        assertEquals("timer_stop", n.actions.get(0).callback);
        assertEquals("2026-10-15T12:00:00.000001+00:00", a.cursor);
    }

    @Test
    public void anEmptyAnswerKeepsTheCursor() throws Exception {
        PushFallback.Answer a = PushFallback.parse("{\"notifications\":[]}", "c1");
        assertTrue(a.notices.isEmpty());
        assertEquals("c1", a.cursor);
    }

    @Test
    public void whatThePollShowedIsNotShownAgain() {
        PushFallback.Shown shown = new PushFallback.Shown();
        assertTrue(shown.add("n1"));
        assertFalse(shown.add("n1"));
        assertTrue(shown.contains("n1"));
        assertFalse(shown.contains("n2"));
        // A notice without an id cannot be matched, so it is always shown.
        assertTrue(shown.add(""));
        assertTrue(shown.add(""));
        assertFalse(shown.contains(""));
        assertFalse(shown.contains(null));
    }

    @Test
    public void onlyTheMostRecentIdsAreKept() {
        PushFallback.Shown shown = new PushFallback.Shown();
        for (int i = 0; i <= PushFallback.SHOWN_LIMIT; i++) shown.add("n" + i);
        assertFalse(shown.contains("n0"));
        assertTrue(shown.contains("n1"));
        assertTrue(shown.contains("n" + PushFallback.SHOWN_LIMIT));
    }
}
