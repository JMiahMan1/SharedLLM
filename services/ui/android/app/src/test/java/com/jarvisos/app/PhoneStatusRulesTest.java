package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class PhoneStatusRulesTest {

    @Test
    public void batteryStatusReadsAsTheCompanionAppsBatteryState() {
        assertEquals("charging", PhoneStatusRules.batteryState(2));
        assertEquals("discharging", PhoneStatusRules.batteryState(3));
        assertEquals("not_charging", PhoneStatusRules.batteryState(4));
        assertEquals("full", PhoneStatusRules.batteryState(5));
        assertEquals("unknown", PhoneStatusRules.batteryState(1));
        assertEquals("unknown", PhoneStatusRules.batteryState(-1));
        assertEquals("unknown", PhoneStatusRules.batteryState(42));
    }

    @Test
    public void pluggedReadsAsTheCompanionAppsChargerType() {
        assertEquals("none", PhoneStatusRules.chargerType(0));
        assertEquals("ac", PhoneStatusRules.chargerType(1));
        assertEquals("usb", PhoneStatusRules.chargerType(2));
        assertEquals("wireless", PhoneStatusRules.chargerType(4));
        assertEquals("dock", PhoneStatusRules.chargerType(8));
        assertEquals("unknown", PhoneStatusRules.chargerType(-1));
    }

    @Test
    public void fullOnTheChargerStillCountsAsCharging() {
        assertTrue(PhoneStatusRules.isCharging(2));
        assertTrue(PhoneStatusRules.isCharging(5));
        assertFalse(PhoneStatusRules.isCharging(3));
        assertFalse(PhoneStatusRules.isCharging(4));
        assertFalse(PhoneStatusRules.isCharging(1));
    }

    @Test
    public void onlyAnAlarmStillToComeIsTheNextAlarm() {
        assertEquals(2_000L, PhoneStatusRules.nextAlarmMs(2_000L, 1_000L));
        assertEquals(0L, PhoneStatusRules.nextAlarmMs(1_000L, 1_000L));
        assertEquals(0L, PhoneStatusRules.nextAlarmMs(500L, 1_000L));
        assertEquals(0L, PhoneStatusRules.nextAlarmMs(0L, 1_000L));
    }

    @Test
    public void anEmptyPackageIsNoPackage() {
        assertEquals("com.example.clock", PhoneStatusRules.alarmPackage(" com.example.clock "));
        assertNull(PhoneStatusRules.alarmPackage(""));
        assertNull(PhoneStatusRules.alarmPackage(null));
    }

    @Test
    public void theFirstChangeIsReportedAtOnce() {
        PhoneStatusRules.Limiter limiter = new PhoneStatusRules.Limiter(30_000L);
        assertEquals(0L, limiter.request(1_000_000L));
    }

    @Test
    public void changesInsideTheGapFoldIntoOneReportAtItsEnd() {
        PhoneStatusRules.Limiter limiter = new PhoneStatusRules.Limiter(30_000L);
        assertEquals(0L, limiter.request(100_000L));
        limiter.sent(100_000L);
        // Unplugged 10 s later: wait out the rest of the gap.
        assertEquals(20_000L, limiter.request(110_000L));
        // Plugged back in meanwhile: the waiting report carries it.
        assertEquals(PhoneStatusRules.Limiter.ALREADY_PENDING, limiter.request(115_000L));
        limiter.sent(130_000L);
        // After the gap, at once again.
        assertEquals(0L, limiter.request(160_000L));
    }

    @Test
    public void aRequestWaitsWhileTheFirstReportIsInFlight() {
        PhoneStatusRules.Limiter limiter = new PhoneStatusRules.Limiter(30_000L);
        assertEquals(0L, limiter.request(1_000L));
        assertEquals(PhoneStatusRules.Limiter.ALREADY_PENDING, limiter.request(1_500L));
    }

    @Test
    public void aClockThatMovedBackNeverWaitsLongerThanTheGap() {
        PhoneStatusRules.Limiter limiter = new PhoneStatusRules.Limiter(30_000L);
        limiter.request(100_000L);
        limiter.sent(100_000L);
        assertEquals(30_000L, limiter.request(50_000L));
    }
}
