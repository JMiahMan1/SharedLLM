package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import android.app.usage.UsageEvents;

import org.junit.Test;

/**
 * The pure folding behind the phone usage window: which events open a session,
 * when the day started and stopped, and how many sessions it had.
 */
public class UsageWindowTest {

    @Test
    public void foldsResumedAndPausedIntoOneSession() {
        UsageWindow window = new UsageWindow();
        window.accept(UsageEvents.Event.ACTIVITY_RESUMED, 1_000L);
        window.accept(UsageEvents.Event.ACTIVITY_PAUSED, 5_000L);
        assertTrue(window.hasData());
        assertEquals(1, window.sessions);
        assertEquals(1_000L, window.firstUse);
        assertEquals(1_000L, window.lastUse);
    }

    @Test
    public void countsConsecutiveResumesAsOneSession() {
        UsageWindow window = new UsageWindow();
        window.accept(UsageEvents.Event.ACTIVITY_RESUMED, 1_000L);
        window.accept(UsageEvents.Event.ACTIVITY_RESUMED, 2_000L);
        window.accept(UsageEvents.Event.ACTIVITY_PAUSED, 3_000L);
        window.accept(UsageEvents.Event.ACTIVITY_RESUMED, 9_000L);
        assertEquals(2, window.sessions);
        assertEquals(1_000L, window.firstUse);
        assertEquals(9_000L, window.lastUse);
    }

    @Test
    public void staysEmptyWithoutResumes() {
        UsageWindow window = new UsageWindow();
        window.accept(UsageEvents.Event.ACTIVITY_STOPPED, 1_000L);
        assertFalse(window.hasData());
        assertEquals(0, window.sessions);
        assertEquals(0L, window.firstUse);
        assertEquals(0L, window.lastUse);
    }
}
