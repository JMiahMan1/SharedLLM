package com.jarvisos.app;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

public class ProximityRulesTest {
    // A made-up household key and members.
    private static final byte[] KEY = "0123456789abcdef0123456789abcdef".getBytes(StandardCharsets.UTF_8);
    private static final byte[] OTHER_KEY = "fedcba9876543210fedcba9876543210".getBytes(StandardCharsets.UTF_8);
    private static final List<String> MEMBERS = Arrays.asList("pat", "gracie", "alex");
    private static final long T = 1_800_000_000_000L;

    @Test
    public void identifiersRotateEveryPeriodAndDifferPerMemberAndHousehold() {
        long p = ProximityRules.period(T, ProximityRules.PERIOD_S);
        byte[] now = ProximityRules.memberId(KEY, "gracie", p);
        assertEquals(ProximityRules.ID_BYTES, now.length);
        assertFalse(Arrays.equals(now, ProximityRules.memberId(KEY, "gracie", p + 1)));
        assertFalse(Arrays.equals(now, ProximityRules.memberId(KEY, "pat", p)));
        assertFalse(Arrays.equals(now, ProximityRules.memberId(OTHER_KEY, "gracie", p)));
        assertArrayEquals(now, ProximityRules.memberId(KEY, "gracie", p));
    }

    @Test
    public void theServiceUuidRotatesAndLooksRandom() {
        UUID a = ProximityRules.serviceUuid(KEY, 10);
        UUID b = ProximityRules.serviceUuid(KEY, 11);
        assertNotEquals(a, b);
        assertNotEquals(a, ProximityRules.serviceUuid(OTHER_KEY, 10));
        assertEquals(4, a.version());
        assertEquals(2, a.variant());
    }

    @Test
    public void theNextPeriodStartsOnTheBoundary() {
        long next = ProximityRules.nextPeriodAtMs(T, 900);
        assertEquals(0, next % 900_000);
        assertTrue(next > T && next - T <= 900_000);
    }

    @Test
    public void aFamilyPhoneResolvesWhatItHearsAcrossAPeriodEdge() {
        long p = ProximityRules.period(T, 900);
        ProximityRules.Resolver r = new ProximityRules.Resolver(KEY, MEMBERS, "pat", p);
        assertEquals(3, r.uuids.size());
        // Gracie's phone is a little behind (previous period).
        byte[] data = ProximityRules.payload(ProximityRules.memberId(KEY, "gracie", p - 1), ProximityRules.FLAG_MOVING, -61);
        ProximityRules.Heard h = r.resolve(ProximityRules.serviceUuid(KEY, p - 1), data);
        assertNotNull(h);
        assertEquals("gracie", h.member);
        assertEquals(ProximityRules.FLAG_MOVING, h.flags);
        assertEquals(-61, h.txAt1m);
        // Its own advertisement, another household, a stale period or junk: nothing.
        assertNull(r.resolve(ProximityRules.serviceUuid(KEY, p),
                ProximityRules.payload(ProximityRules.memberId(KEY, "pat", p), 0, -59)));
        assertNull(r.resolve(ProximityRules.serviceUuid(OTHER_KEY, p),
                ProximityRules.payload(ProximityRules.memberId(OTHER_KEY, "gracie", p), 0, -59)));
        assertNull(r.resolve(ProximityRules.serviceUuid(KEY, p - 5),
                ProximityRules.payload(ProximityRules.memberId(KEY, "gracie", p - 5), 0, -59)));
        assertNull(r.resolve(ProximityRules.serviceUuid(KEY, p), new byte[] {1, 2}));
    }

    @Test
    public void thePayloadFitsALegacyAdvertisementWithItsUuid() {
        byte[] data = ProximityRules.payload(new byte[ProximityRules.ID_BYTES], 0, -59);
        // flags (3) + service data AD: length, type, 16-byte UUID, payload.
        assertTrue(3 + 2 + 16 + data.length <= 31);
    }

    @Test
    public void rssiBandsFromPathLoss() {
        assertEquals("next_to", ProximityRules.band(-60, -59));
        assertEquals("same_room", ProximityRules.band(-75, -59));
        assertEquals("nearby", ProximityRules.band(-88, -59));
        assertNull(ProximityRules.band(-100, -59));
        // A stronger transmitter is further away for the same reading.
        assertEquals("same_room", ProximityRules.band(-60, -45));
    }

    @Test
    public void readingsCloseIntoWindowsByMedian() throws Exception {
        ProximityRules.Aggregator agg = new ProximityRules.Aggregator();
        agg.add("gracie", -62, -59, T);
        agg.add("gracie", -95, -59, T + 5_000);   // one bad reading (a pocket) does not move the median
        agg.add("gracie", -64, -59, T + 10_000);
        agg.add("alex", -110, -59, T + 10_000);
        assertTrue(agg.close(T + 20_000, false).isEmpty());
        List<ProximityRules.Sighting> done = agg.close(T + 10_000 + ProximityRules.GAP_MS, false);
        assertEquals(1, done.size()); // alex was too faint to be anywhere
        ProximityRules.Sighting s = done.get(0);
        assertEquals("gracie", s.person);
        assertEquals("next_to", s.band);
        assertEquals(3, s.samples);
        JSONObject json = s.toJson();
        assertEquals(T / 1000.0, json.getDouble("first_seen"), 0.001);
        assertEquals(0, agg.openCount());
    }

    @Test
    public void aLongSightingIsCutIntoWindows() {
        ProximityRules.Aggregator agg = new ProximityRules.Aggregator();
        for (long t = 0; t <= ProximityRules.WINDOW_MS; t += 10_000) agg.add("gracie", -70, -59, T + t);
        assertEquals(1, agg.close(T + ProximityRules.WINDOW_MS, false).size());
    }

    @Test
    public void scanningIsGentleIdleAndSteadyOnATrip() {
        ProximityRules.ScanPlan idle = ProximityRules.scanPlan(false);
        assertFalse(idle.balanced);
        assertTrue((double) idle.onMs / (idle.onMs + idle.offMs) <= 0.2);
        ProximityRules.ScanPlan trip = ProximityRules.scanPlan(true);
        assertTrue(trip.balanced);
        assertEquals(0, trip.offMs);
    }

    @Test
    public void aTabletThatCannotAdvertiseScansAndSaysSo() {
        assertEquals("unsupported", ProximityRules.advertiseCapability(true, false, false));
        assertEquals("unsupported", ProximityRules.advertiseCapability(true, true, false));
        assertEquals("on", ProximityRules.advertiseCapability(true, true, true));
        String[] h = ProximityRules.healthOutcome(true, true, true, "unsupported");
        assertEquals("cannot_advertise", h[0]);
        assertTrue(h[1].contains("sees family phones"));
        assertEquals("ok", ProximityRules.healthOutcome(true, true, true, "on")[0]);
        assertEquals("no_permission", ProximityRules.healthOutcome(true, false, true, "on")[0]);
        assertEquals("off", ProximityRules.healthOutcome(false, true, true, "on")[0]);
    }

    @Test
    public void theConfigIsParsedOrRefused() throws Exception {
        String key = java.util.Base64.getEncoder().encodeToString(KEY);
        ProximityRules.Config c = ProximityRules.parseConfig(new JSONObject()
                .put("key", key).put("key_id", "abc").put("period_s", 900)
                .put("members", new org.json.JSONArray(Arrays.asList("Pat", "gracie", "pat"))).put("me", "pat"));
        assertNotNull(c);
        assertEquals(Arrays.asList("pat", "gracie"), c.members);
        assertArrayEquals(KEY, c.key);
        assertNull(ProximityRules.parseConfig(new JSONObject().put("key", "short")));
        assertNull(ProximityRules.parseConfig(null));
    }
}
