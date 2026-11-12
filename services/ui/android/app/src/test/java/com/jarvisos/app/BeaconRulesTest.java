package com.jarvisos.app;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Random;

import org.json.JSONObject;
import org.junit.Test;

public class BeaconRulesTest {

    private static final String UUID = "00000000-0000-4000-8000-000000000001";
    private static final long H = 60L * 60 * 1000;

    // -- identity -----------------------------------------------------------------------

    @Test
    public void aNewUuidIsAVersionFourLowercaseUuid() {
        String u = BeaconRules.newUuid(new Random(7));
        assertTrue(u, BeaconRules.isValidUuid(u));
        assertEquals('4', u.charAt(14));
        assertTrue("89ab".indexOf(u.charAt(19)) >= 0);
    }

    @Test
    public void theSameRandomGivesTheSameUuidAndDifferentRandomsDiffer() {
        assertEquals(BeaconRules.newUuid(new Random(1)), BeaconRules.newUuid(new Random(1)));
        assertNotEquals(BeaconRules.newUuid(new Random(1)), BeaconRules.newUuid(new Random(2)));
    }

    @Test
    public void onlyTheLowercaseDashedFormIsValid() {
        assertTrue(BeaconRules.isValidUuid(UUID));
        assertFalse(BeaconRules.isValidUuid("0000000a-0000-4000-8000-00000000000b".toUpperCase()));
        assertFalse(BeaconRules.isValidUuid(UUID.replace("-", "")));
        assertFalse(BeaconRules.isValidUuid(null));
        assertFalse(BeaconRules.isValidUuid(""));
    }

    @Test
    public void majorAndMinorAreNeverZeroAndFitSixteenBits() {
        Random r = new Random(3);
        for (int i = 0; i < 10_000; i++) {
            int v = BeaconRules.newMajorMinor(r);
            assertTrue(v >= 1 && v <= 0xFFFF);
        }
        assertFalse(BeaconRules.isValidMajorMinor(-1));
        assertFalse(BeaconRules.isValidMajorMinor(0x10000));
    }

    // -- power and the frame -------------------------------------------------------------

    @Test
    public void powerLevelsMapToAndroidsConstantsAndDefaultToLow() {
        assertEquals(0, BeaconRules.TxPower.ULTRA_LOW.android);
        assertEquals(1, BeaconRules.TxPower.LOW.android);
        assertEquals(2, BeaconRules.TxPower.MEDIUM.android);
        assertEquals(3, BeaconRules.TxPower.HIGH.android);
        assertEquals(BeaconRules.TxPower.LOW, BeaconRules.DEFAULT_TX_POWER);
        assertEquals(BeaconRules.TxPower.MEDIUM, BeaconRules.TxPower.fromId(" Medium "));
        assertNull(BeaconRules.TxPower.fromId("loud"));
        assertNull(BeaconRules.TxPower.fromId(null));
    }

    @Test
    public void strongerLevelsAreHeardLouderAtOneMetre() {
        BeaconRules.TxPower[] v = BeaconRules.TxPower.values();
        for (int i = 1; i < v.length; i++) assertTrue(v[i].measuredPower > v[i - 1].measuredPower);
    }

    @Test
    public void theFrameIsAnIBeacon() {
        byte[] d = BeaconRules.manufacturerData("01020304-0506-4708-890a-0b0c0d0e0f10", 0x1234, 0xABCD, -59);
        assertEquals(23, d.length);
        assertEquals(0x02, d[0]);
        assertEquals(0x15, d[1]);
        assertArrayEquals(new byte[] {1, 2, 3, 4, 5, 6, 0x47, 8, (byte) 0x89, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10},
                java.util.Arrays.copyOfRange(d, 2, 18));
        assertEquals(0x12, d[18]);
        assertEquals(0x34, d[19]);
        assertEquals((byte) 0xAB, d[20]);
        assertEquals((byte) 0xCD, d[21]);
        assertEquals((byte) -59, d[22]);
        assertEquals(0x004C, BeaconRules.APPLE_COMPANY_ID);
    }

    @Test(expected = IllegalArgumentException.class)
    public void aBadUuidIsRefusedNotAdvertised() {
        BeaconRules.manufacturerData("not-a-uuid", 1, 1, -59);
    }

    @Test(expected = IllegalArgumentException.class)
    public void anOutOfRangeMinorIsRefused() {
        BeaconRules.manufacturerData(UUID, 1, 70_000, -59);
    }

    // -- state ------------------------------------------------------------------------------

    private static BeaconRules.Status st(boolean enabled, boolean has, boolean perm, boolean on, boolean can, int err,
                                         boolean started) {
        return BeaconRules.status(enabled, has, perm, on, can, err, started);
    }

    @Test
    public void advertisingOnceTheStartIsConfirmed() {
        assertEquals(BeaconRules.State.STARTING, st(true, true, true, true, true, 0, false).state);
        BeaconRules.Status s = st(true, true, true, true, true, 0, true);
        assertEquals(BeaconRules.State.ADVERTISING, s.state);
        assertNull(s.reason);
    }

    @Test
    public void switchedOffIsDisabledWhateverElseIsWrong() {
        assertEquals(BeaconRules.State.DISABLED, st(false, false, false, false, false, 4, false).state);
    }

    @Test
    public void everyWayItCannotRunHasAReason() {
        BeaconRules.Status noBt = st(true, false, true, true, true, 0, false);
        assertEquals(BeaconRules.State.UNAVAILABLE, noBt.state);
        assertEquals(BeaconRules.REASON_NO_BLUETOOTH, noBt.reason);

        BeaconRules.Status noPerm = st(true, true, false, true, true, 0, false);
        assertEquals(BeaconRules.State.UNAVAILABLE, noPerm.state);
        assertTrue(noPerm.reason.contains("permission"));

        BeaconRules.Status chip = st(true, true, true, true, false, 0, false);
        assertEquals(BeaconRules.State.UNAVAILABLE, chip.state);
        assertEquals("this phone's Bluetooth chip cannot advertise", chip.reason);

        for (int code : new int[] {1, 2, 4, 5, 99}) {
            BeaconRules.Status s = st(true, true, true, true, true, code, false);
            assertEquals(BeaconRules.State.UNAVAILABLE, s.state);
            assertTrue(s.reason != null && !s.reason.isEmpty());
        }
        assertEquals(BeaconRules.REASON_CANNOT_ADVERTISE, BeaconRules.advertiseErrorReason(5));
    }

    @Test
    public void bluetoothOffWaitsRatherThanFails() {
        BeaconRules.Status s = st(true, true, true, false, false, 0, false);
        assertEquals(BeaconRules.State.BLUETOOTH_OFF, s.state);
        assertTrue(s.reason.contains("Bluetooth is off"));
        assertFalse(BeaconRules.shouldAdvertise(true, true, true, false, true));
        assertTrue(BeaconRules.shouldAdvertise(true, true, true, true, true));
    }

    @Test
    public void aMissingPermissionIsSaidBeforeBluetoothBeingOff() {
        assertEquals(BeaconRules.State.UNAVAILABLE, st(true, true, false, false, true, 0, false).state);
    }

    @Test
    public void alreadyStartedCountsAsAdvertising() {
        assertEquals(BeaconRules.State.ADVERTISING, st(true, true, true, true, true, 3, false).state);
    }

    // -- children -------------------------------------------------------------------------

    @Test
    public void aChildsPhoneAlwaysTransmitsAndCannotSwitchItOff() {
        assertTrue(BeaconRules.effectiveEnabled(false, true));
        assertFalse(BeaconRules.canDisable(true));
        assertTrue(BeaconRules.canDisable(false));
        assertFalse(BeaconRules.effectiveEnabled(false, false));
        assertTrue(BeaconRules.effectiveEnabled(true, false));
    }

    @Test
    public void aScreenTimeLockDoesNotStopTheBeacon() {
        // The lock is in force, and Jarvis mode is not: the beacon's rules take no lock input,
        // so a protected, locked phone keeps transmitting either way.
        assertTrue(LockedJarvisRules.lockInForce(true, true, false));
        boolean enabled = BeaconRules.effectiveEnabled(false, true);
        assertTrue(BeaconRules.shouldAdvertise(enabled, true, true, true, true));
        assertEquals(BeaconRules.State.ADVERTISING, st(enabled, true, true, true, true, 0, true).state);
        // And the lock-screen rules are unchanged by it.
        assertFalse(LockedJarvisRules.showWhenLocked(false, false));
        assertTrue(LockedJarvisRules.active(true, true, true, false));
    }

    // -- reports ---------------------------------------------------------------------------

    @Test
    public void theReportCarriesTheBeaconAndItsState() throws Exception {
        BeaconRules.Status s = st(true, true, false, true, true, 0, false);
        JSONObject b = BeaconRules.reportBody(UUID, 10, 20, BeaconRules.TxPower.LOW, s, " Example Phone ");
        assertEquals(UUID, b.getString("uuid"));
        assertEquals(10, b.getInt("major"));
        assertEquals(20, b.getInt("minor"));
        assertEquals(-75, b.getInt("tx_power"));
        assertEquals("low", b.getString("tx_power_level"));
        assertEquals("unavailable", b.getString("state"));
        assertEquals(BeaconRules.REASON_PERMISSION, b.getString("reason"));
        assertEquals("Example Phone", b.getString("device_name"));

        JSONObject ok = BeaconRules.reportBody(UUID, 1, 2, BeaconRules.TxPower.HIGH,
                st(true, true, true, true, true, 0, true), null);
        assertTrue(ok.isNull("reason"));
        assertEquals("advertising", ok.getString("state"));
        assertEquals("Android", ok.getString("device_name"));
    }

    @Test(expected = IllegalArgumentException.class)
    public void aStartingBeaconIsNotReported() throws Exception {
        BeaconRules.reportBody(UUID, 1, 2, BeaconRules.TxPower.LOW, st(true, true, true, true, true, 0, false), "x");
    }

    @Test
    public void aChangeIsReportedAtOnceAndTheSameStateEverySixHours() {
        String k = "a", k2 = "b";
        long t = 1_000_000L;
        assertTrue(BeaconRules.reportDue(k, null, 0, 0, 0, t));
        assertTrue(BeaconRules.reportDue(k2, k, t, 0, t, t + 1));
        assertFalse(BeaconRules.reportDue(k, k, t, 0, t, t + 5 * H));
        assertTrue(BeaconRules.reportDue(k, k, t, 0, t, t + 6 * H));
        // The clock moved back: send.
        assertTrue(BeaconRules.reportDue(k, k, t, 0, t, t - 10));
    }

    @Test
    public void failuresBackOffAndNeverSpam() {
        long t = 1_000_000L;
        assertEquals(5 * 60 * 1000L, BeaconRules.retryDelay(1));
        assertEquals(10 * 60 * 1000L, BeaconRules.retryDelay(2));
        assertEquals(BeaconRules.REFRESH_MS, BeaconRules.retryDelay(50));
        // A change during a back-off waits for it too.
        assertFalse(BeaconRules.reportDue("new", "old", 0, 1, t, t + 60_000));
        assertTrue(BeaconRules.reportDue("new", "old", 0, 1, t, t + 5 * 60 * 1000L));
    }

    @Test
    public void theKeyChangesWithIdentityPowerAndState() {
        BeaconRules.Status a = st(true, true, true, true, true, 0, true);
        BeaconRules.Status b = st(true, true, true, false, true, 0, false);
        String base = BeaconRules.reportKey(UUID, 1, 2, BeaconRules.TxPower.LOW, a);
        assertNotEquals(base, BeaconRules.reportKey(UUID, 1, 3, BeaconRules.TxPower.LOW, a));
        assertNotEquals(base, BeaconRules.reportKey(UUID, 1, 2, BeaconRules.TxPower.HIGH, a));
        assertNotEquals(base, BeaconRules.reportKey(UUID, 1, 2, BeaconRules.TxPower.LOW, b));
        assertEquals(base, BeaconRules.reportKey(UUID, 1, 2, BeaconRules.TxPower.LOW, a));
    }

    @Test
    public void theHubsAnswerIsReadAndNothingElseCountsAsRegistered() {
        assertArrayEquals(new String[] {"true", "registered on Example Phone"},
                BeaconRules.hubResult("{\"stored\":true,\"hub\":{\"registered\":true,\"detail\":\"registered on Example Phone\"}}"));
        assertEquals("false", BeaconRules.hubResult("{\"stored\":true,\"hub\":{\"registered\":false,\"detail\":\"no phone\"}}")[0]);
        assertEquals("false", BeaconRules.hubResult("{\"stored\":true}")[0]);
        assertEquals("false", BeaconRules.hubResult("<html>")[0]);
        assertEquals("false", BeaconRules.hubResult(null)[0]);
        assertEquals("false", BeaconRules.hubResult("")[0]);
    }
}
