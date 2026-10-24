package com.jarvisos.app;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

import android.app.usage.UsageEvents;

import java.util.List;

import org.junit.Test;

public class NightUseTest {

    private static final int RESUMED = UsageEvents.Event.ACTIVITY_RESUMED;
    private static final int PAUSED = UsageEvents.Event.ACTIVITY_PAUSED;
    private static final int SCREEN_OFF = UsageEvents.Event.SCREEN_NON_INTERACTIVE;

    @Test
    public void anAppInFrontIsAStretchOfUse() {
        NightUse night = new NightUse();
        night.accept(RESUMED, 1_000);
        night.accept(PAUSED, 5_000);
        List<long[]> out = night.finish(10_000);
        assertEquals(1, out.size());
        assertArrayEquals(new long[] {1_000, 5_000}, out.get(0));
    }

    @Test
    public void switchingAppsStaysOneStretch() {
        NightUse night = new NightUse();
        night.accept(RESUMED, 0);
        night.accept(PAUSED, 10_000);
        night.accept(RESUMED, 10_500);
        night.accept(PAUSED, 20_000);
        assertEquals(1, night.finish(30_000).size());
    }

    @Test
    public void theScreenGoingOffEndsAStretch() {
        NightUse night = new NightUse();
        night.accept(RESUMED, 0);
        night.accept(SCREEN_OFF, 4_000);
        night.accept(RESUMED, 4_000_000);
        night.accept(PAUSED, 4_100_000);
        List<long[]> out = night.finish(5_000_000);
        assertEquals(2, out.size());
        assertArrayEquals(new long[] {0, 4_000}, out.get(0));
    }

    @Test
    public void aStretchStillOpenEndsNow() {
        NightUse night = new NightUse();
        night.accept(RESUMED, 100);
        assertArrayEquals(new long[] {100, 900}, night.finish(900).get(0));
    }

    @Test
    public void aPauseWithoutAResumeIsIgnored() {
        NightUse night = new NightUse();
        night.accept(PAUSED, 100);
        assertEquals(0, night.finish(900).size());
    }
}
