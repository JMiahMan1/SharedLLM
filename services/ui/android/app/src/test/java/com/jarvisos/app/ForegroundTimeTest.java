package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import android.app.usage.UsageEvents;
import org.junit.Test;

public class ForegroundTimeTest {

    @Test
    public void sumsResumedToPausedStretches() {
        ForegroundTime time = new ForegroundTime();
        time.accept(UsageEvents.Event.ACTIVITY_RESUMED, 1_000L, "com.a");
        time.accept(UsageEvents.Event.ACTIVITY_PAUSED, 4_000L, "com.a");
        time.finish(10_000L);
        assertEquals(3_000L, time.totalMillis());
        assertEquals(3_000L, time.millisFor("com.a"));
    }

    @Test
    public void aNewPackageClosesTheOpenStretch() {
        ForegroundTime time = new ForegroundTime();
        time.accept(UsageEvents.Event.ACTIVITY_RESUMED, 1_000L, "com.a");
        time.accept(UsageEvents.Event.ACTIVITY_RESUMED, 3_000L, "com.b");
        time.accept(UsageEvents.Event.ACTIVITY_PAUSED, 5_000L, "com.b");
        assertEquals(2_000L, time.millisFor("com.a"));
        assertEquals(2_000L, time.millisFor("com.b"));
        assertEquals(4_000L, time.totalMillis());
    }

    @Test
    public void screenNonInteractiveClosesTheStretch() {
        ForegroundTime time = new ForegroundTime();
        time.accept(UsageEvents.Event.ACTIVITY_RESUMED, 1_000L, "com.a");
        time.accept(UsageEvents.Event.SCREEN_NON_INTERACTIVE, 2_500L, null);
        time.finish(9_000L);
        assertEquals(1_500L, time.totalMillis());
    }

    @Test
    public void aStretchStillOpenAtFinishCountsUpToFinish() {
        ForegroundTime time = new ForegroundTime();
        time.accept(UsageEvents.Event.ACTIVITY_RESUMED, 1_000L, "com.a");
        time.finish(6_000L);
        assertEquals(5_000L, time.totalMillis());
    }

    @Test
    public void ignoresAResumedWithoutAPackage() {
        ForegroundTime time = new ForegroundTime();
        // A RESUMED with no package opens nothing, so nothing is counted.
        time.accept(UsageEvents.Event.ACTIVITY_RESUMED, 1_000L, null);
        time.accept(UsageEvents.Event.ACTIVITY_PAUSED, 5_000L, null);
        assertEquals(0L, time.totalMillis());
        assertEquals(0L, time.millisFor("com.a"));
        assertNull(time.lastUseFor("com.a"));
    }

    @Test
    public void recordsLastUsePerPackage() {
        ForegroundTime time = new ForegroundTime();
        time.accept(UsageEvents.Event.ACTIVITY_RESUMED, 1_000L, "com.a");
        time.accept(UsageEvents.Event.ACTIVITY_PAUSED, 2_000L, "com.a");
        time.accept(UsageEvents.Event.ACTIVITY_RESUMED, 4_000L, "com.a");
        time.accept(UsageEvents.Event.ACTIVITY_PAUSED, 6_000L, "com.a");
        assertEquals(4_000L, time.lastUseFor("com.a").longValue());
        assertEquals(3_000L, time.millisFor("com.a"));
    }
}
