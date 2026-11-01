package com.jarvisos.app;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class DeviceNounTest {

    @Test
    public void sixHundredDpAndUpIsATablet() {
        assertEquals("tablet", DeviceNoun.forWidth(600));
        assertEquals("tablet", DeviceNoun.forWidth(800));
    }

    @Test
    public void narrowerIsAPhone() {
        assertEquals("phone", DeviceNoun.forWidth(599));
        assertEquals("phone", DeviceNoun.forWidth(360));
    }

    @Test
    public void anUnreadableWidthIsAPhone() {
        assertEquals("phone", DeviceNoun.forWidth(0));
    }

    @Test
    public void theLockWarningNamesTheDevice() {
        long at = 1_000_000L;
        assertEquals("Your tablet locks in one minute.", LockWarning.due(at - 30_000, at, "paused", "tablet").text);
        assertEquals("Your phone locks in one minute.", LockWarning.due(at - 30_000, at, "paused", "phone").text);
    }
}
