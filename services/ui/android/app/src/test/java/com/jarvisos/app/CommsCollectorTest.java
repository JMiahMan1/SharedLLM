package com.jarvisos.app;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class CommsCollectorTest {

    @Test
    public void theCursorTakesLaterRowsAndLaterIdsInTheSameMillisecond() {
        // Two rows in one millisecond must not be skipped when a run stops between them.
        assertEquals("date > ? OR (date = ? AND _id > ?)", CommsCollector.after("date", "_id"));
        assertArrayEquals(new String[] {"1700000000000", "1700000000000", "42"},
            CommsCollector.afterArgs(1_700_000_000_000L, 42L));
    }

    @Test
    public void theFirstRunLooksBackAWeekAndLaterRunsStartAtTheCursor() {
        long before = System.currentTimeMillis() - CommsCollector.FIRST_RUN_WINDOW_MS;
        long start = CommsCollector.startFrom(0L);
        assertTrue(start >= before && start <= before + 5_000);
        assertEquals(1_700_000_000_000L, CommsCollector.startFrom(1_700_000_000_000L));
    }

    @Test
    public void directionsReadTheWayTheFeedShowsThem() {
        assertEquals("in", CommsCollector.callDirection(android.provider.CallLog.Calls.INCOMING_TYPE));
        assertEquals("missed", CommsCollector.callDirection(android.provider.CallLog.Calls.MISSED_TYPE));
        assertEquals("unknown", CommsCollector.callDirection(99));
        assertEquals("in", CommsCollector.smsDirection(1));
        assertEquals("out", CommsCollector.smsDirection(2));
        assertEquals("unknown", CommsCollector.smsDirection(0));
    }
}
