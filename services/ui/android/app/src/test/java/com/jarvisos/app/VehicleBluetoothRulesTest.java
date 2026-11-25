package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

public class VehicleBluetoothRulesTest {
    // Made-up addresses: a head unit (classic), its LE side (random static), a rotating LE address.
    private static final String HEAD_UNIT = "00:1A:7D:DA:71:13";
    private static final String HEAD_UNIT_LE = "C4:7D:4F:12:34:56";
    private static final String ROTATING = "4A:11:22:33:44:55";
    private static final String HEADSET = "00:1B:DC:0F:AA:01";

    @Test
    public void addressesAreNormalisedAndHiddenOnesRefused() {
        assertEquals(HEAD_UNIT, VehicleBluetoothRules.normalise("00-1a-7d-da-71-13"));
        assertEquals(HEAD_UNIT, VehicleBluetoothRules.normalise("001A7DDA7113"));
        assertNull(VehicleBluetoothRules.normalise("02:00:00:00:00:00"));
        assertNull(VehicleBluetoothRules.normalise("Car Audio"));
        assertNull(VehicleBluetoothRules.normalise(null));
    }

    @Test
    public void aPairedClassicHeadUnitCanBeLinked() {
        VehicleBluetoothRules.Linkable l = VehicleBluetoothRules.linkable(HEAD_UNIT,
                VehicleBluetoothRules.TYPE_CLASSIC, true, VehicleBluetoothRules.ADDR_UNKNOWN);
        assertTrue(l.ok);
        assertEquals("classic", l.kind);
        assertTrue(VehicleBluetoothRules.linkable(HEAD_UNIT, VehicleBluetoothRules.TYPE_DUAL, true,
                VehicleBluetoothRules.ADDR_PUBLIC).ok);
    }

    @Test
    public void leIsLinkableOnlyWithAStableAddress() {
        assertEquals("le_public", VehicleBluetoothRules.linkable(HEAD_UNIT, VehicleBluetoothRules.TYPE_LE, true,
                VehicleBluetoothRules.ADDR_PUBLIC).kind);
        assertEquals("le_static", VehicleBluetoothRules.linkable(HEAD_UNIT_LE, VehicleBluetoothRules.TYPE_LE, true,
                VehicleBluetoothRules.ADDR_RANDOM).kind);
        VehicleBluetoothRules.Linkable rotating = VehicleBluetoothRules.linkable(ROTATING,
                VehicleBluetoothRules.TYPE_LE, true, VehicleBluetoothRules.ADDR_RANDOM);
        assertFalse(rotating.ok);
        assertEquals(VehicleBluetoothRules.REASON_ROTATING, rotating.reason);
    }

    @Test
    public void unpairedOrUnreadableDevicesSayWhy() {
        assertEquals(VehicleBluetoothRules.REASON_NOT_PAIRED, VehicleBluetoothRules.linkable(HEAD_UNIT,
                VehicleBluetoothRules.TYPE_CLASSIC, false, VehicleBluetoothRules.ADDR_PUBLIC).reason);
        assertEquals(VehicleBluetoothRules.REASON_NO_PERMISSION, VehicleBluetoothRules.linkable(
                "02:00:00:00:00:00", VehicleBluetoothRules.TYPE_CLASSIC, true, VehicleBluetoothRules.ADDR_PUBLIC).reason);
    }

    @Test
    public void connectAndDisconnectBuildTheConnectedSet() {
        VehicleBluetoothRules.Connected c = new VehicleBluetoothRules.Connected();
        c.connected("00:1a:7d:da:71:13", 1_000L);
        c.connected(HEADSET, 2_000L);
        c.connected(HEAD_UNIT, 5_000L); // a repeat keeps the first time
        c.disconnected(HEADSET);
        assertEquals(Collections.singleton(HEAD_UNIT), c.active(10_000L));
        assertEquals(1_000L, (long) c.since.get(HEAD_UNIT));
    }

    @Test
    public void aMissedDisconnectExpires() {
        VehicleBluetoothRules.Connected c = new VehicleBluetoothRules.Connected();
        c.connected(HEAD_UNIT, 0L + 1);
        assertTrue(c.active(VehicleBluetoothRules.CONNECTION_MAX_AGE_MS).contains(HEAD_UNIT));
        assertTrue(c.active(VehicleBluetoothRules.CONNECTION_MAX_AGE_MS + 2).isEmpty());
    }

    @Test
    public void aSeedReplacesTheBroadcastsButKeepsWhenEachConnected() {
        VehicleBluetoothRules.Connected c = new VehicleBluetoothRules.Connected();
        c.connected(HEAD_UNIT, 100L);
        c.connected(HEADSET, 200L);
        c.seed(Arrays.asList(HEAD_UNIT, HEAD_UNIT_LE), 900L);
        assertEquals(new TreeSet<>(Arrays.asList(HEAD_UNIT, HEAD_UNIT_LE)), c.active(1_000L));
        assertEquals(100L, (long) c.since.get(HEAD_UNIT));
        assertEquals(900L, (long) c.since.get(HEAD_UNIT_LE));
    }

    @Test
    public void theConnectedSetSurvivesStorage() {
        VehicleBluetoothRules.Connected c = new VehicleBluetoothRules.Connected();
        c.connected(HEAD_UNIT, 100L);
        c.connected(HEADSET, 200L);
        VehicleBluetoothRules.Connected back = VehicleBluetoothRules.Connected.decode(c.encode() + ";junk@x;@5");
        assertEquals(c.since, back.since);
    }

    @Test
    public void onlyLinkedDevicesAreSentWithAFix() {
        Set<String> connected = new HashSet<>(Arrays.asList(HEADSET, HEAD_UNIT));
        Set<String> linked = new HashSet<>(Arrays.asList(HEAD_UNIT, HEAD_UNIT_LE));
        List<String> sent = VehicleBluetoothRules.linkedConnected(connected, linked);
        assertEquals(Collections.singletonList(HEAD_UNIT), sent);
        assertTrue(VehicleBluetoothRules.linkedConnected(connected, Collections.emptySet()).isEmpty());
    }

    @Test
    public void linkedAddressesAreReadFromTheVehiclesAnswer() throws Exception {
        JSONObject answer = new JSONObject("{\"vehicles\":[{\"id\":\"van\",\"name\":\"Van\",\"bluetooth\":["
                + "{\"address\":\"00:1a:7d:da:71:13\",\"name\":\"Car Audio\"},{\"address\":\"bad\"}]},"
                + "{\"id\":\"truck\",\"name\":\"Truck\"}]}");
        assertEquals(Collections.singleton(HEAD_UNIT), VehicleBluetoothRules.linkedAddresses(answer));
        assertEquals("Van", VehicleBluetoothRules.linkedVehicleNames(answer).get(HEAD_UNIT));
        assertTrue(VehicleBluetoothRules.linkedAddresses(null).isEmpty());
        Set<String> round = VehicleBluetoothRules.decodeSet(
                VehicleBluetoothRules.encodeSet(VehicleBluetoothRules.linkedAddresses(answer)));
        assertEquals(Collections.singleton(HEAD_UNIT), round);
    }

    @Test
    public void linksAreRefreshedOnScheduleOrWhenAnUnknownDeviceConnects() {
        long last = 1_000_000L;
        assertTrue(VehicleBluetoothRules.refreshDue(0L, last, false));
        assertFalse(VehicleBluetoothRules.refreshDue(last, last + 60_000L, true));
        assertTrue(VehicleBluetoothRules.refreshDue(last, last + VehicleBluetoothRules.LINKS_REFRESH_ON_CONNECT_MS, true));
        assertFalse(VehicleBluetoothRules.refreshDue(last, last + VehicleBluetoothRules.LINKS_REFRESH_ON_CONNECT_MS, false));
        assertTrue(VehicleBluetoothRules.refreshDue(last, last + VehicleBluetoothRules.LINKS_REFRESH_MS, false));
    }
}
