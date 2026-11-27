package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Two devices of one person (a phone and a tablet, made-up ids) keeping their
 * data in step: newer wins both ways, nothing flaps, ties go the same way on
 * every device, steps are never double counted, and a family phone carrying the
 * items cannot read them.
 */
public class OwnSyncRulesTest {
    /** The household proximity key (every family phone has it). */
    private static final byte[] HOUSEHOLD = "0123456789abcdef0123456789abcdef".getBytes(StandardCharsets.UTF_8);
    /** The person's own key, from their (made-up) API key; a sibling's for the relay. */
    private static final byte[] MINE = OwnSyncRules.key("jv_test_key_for_pat");
    private static final byte[] SIBLING = OwnSyncRules.key("jv_test_key_for_robin");
    private static final String PHONE = "11111111-2222-3333-4444-555555555555";
    private static final String TABLET = "66666666-7777-8888-9999-000000000000";
    private static final long NOW_MS = 1_800_000_000_000L;
    private static final long NOW_S = NOW_MS / 1000;
    private static final long PERIOD = ProximityRules.period(NOW_MS, 900);
    private static final String TODAY = "2027-01-15";
    private final SecureRandom random = new SecureRandom();

    private static MeshRules.Store ownStore(byte[] key) {
        MeshRules.Store s = new MeshRules.Store();
        s.acceptor = item -> OwnSyncRules.acceptable(key, item);
        return s;
    }

    private MeshRules.Item item(String name, String device, long at, JSONObject data) throws Exception {
        return OwnSyncRules.seal(MINE, name, device, device.equals(PHONE) ? "Test Phone" : "Test Tablet", at, data,
                NOW_S, random);
    }

    private MeshRules.Item fix(String device, long at, double lat) throws Exception {
        return item("device:" + device, device, at,
                new JSONObject().put("fix", new JSONObject().put("lat", lat).put("lon", -105.0).put("at", at)).put("battery", 60));
    }

    /** One full sync, the client connecting; returns {client, server} sessions. */
    private static MeshRules.Session[] sync(MeshRules.Store client, MeshRules.Store server, boolean own, byte[] clientKey,
                                            byte[] serverKey) throws Exception {
        MeshRules.Session c = new MeshRules.Session(client, HOUSEHOLD, PERIOD, 40, NOW_MS);
        MeshRules.Session s = new MeshRules.Session(server, HOUSEHOLD, PERIOD, 40, NOW_MS);
        if (clientKey != null) c.setOwn(clientKey, PHONE, "Test Phone", own, new SecureRandom());
        if (serverKey != null) s.setOwn(serverKey, TABLET, "Test Tablet", false, new SecureRandom());
        for (byte[] p : c.clientOpening()) assertTrue(s.serverWrite(p));
        List<byte[]> back = Collections.emptyList();
        byte[] piece;
        while ((piece = s.serverNextRead()) != null) {
            List<byte[]> answer = c.clientRead(piece);
            if (!answer.isEmpty()) back = answer;
        }
        for (byte[] p : back) assertTrue(s.serverWrite(p));
        assertTrue(c.done && s.done);
        return new MeshRules.Session[] {c, s};
    }

    private static OwnSyncRules.Opened opened(MeshRules.Store s, String name) {
        return OwnSyncRules.openAll(MINE, s.items.values()).get(name);
    }

    @Test
    public void twoDevicesOfOnePersonReconcileBothWaysAndTheNewerCopyWins() throws Exception {
        MeshRules.Store phone = ownStore(MINE);
        MeshRules.Store tablet = ownStore(MINE);
        // The phone's own fix is newer than the copy of it the tablet holds...
        phone.add(fix(PHONE, NOW_MS - 60_000, 39.70), NOW_S);
        tablet.add(fix(PHONE, NOW_MS - 600_000, 39.60), NOW_S);
        // ...while the tablet read the watch after the phone did.
        phone.add(item("watch", PHONE, NOW_MS - 900_000, new JSONObject().put("steps", 3000).put("day", TODAY)), NOW_S);
        tablet.add(item("watch", TABLET, NOW_MS - 120_000, new JSONObject().put("steps", 4200).put("day", TODAY)), NOW_S);
        tablet.add(fix(TABLET, NOW_MS - 30_000, 39.80), NOW_S);

        MeshRules.Session[] s = sync(phone, tablet, true, MINE, MINE);

        for (MeshRules.Store st : Arrays.asList(phone, tablet)) {
            assertEquals(39.70, opened(st, "device:" + PHONE).data.getJSONObject("fix").getDouble("lat"), 1e-9);
            assertEquals(4200, opened(st, "watch").data.getInt("steps"));
            assertEquals(39.80, opened(st, "device:" + TABLET).data.getJSONObject("fix").getDouble("lat"), 1e-9);
            assertEquals(3, OwnSyncRules.openAll(MINE, st.items.values()).size());
        }
        // Each side took exactly the items it was behind on.
        assertEquals(2, s[0].received);  // the phone: the newer watch, the tablet's fix
        assertEquals(1, s[1].received);  // the tablet: the phone's newer fix
        // The person's latest location is the newest fix of any of their devices, on both.
        assertEquals(TABLET, OwnSyncRules.latestFix(OwnSyncRules.openAll(MINE, phone.items.values()).values()).deviceId);
        assertEquals(TABLET, OwnSyncRules.latestFix(OwnSyncRules.openAll(MINE, tablet.items.values()).values()).deviceId);
    }

    @Test
    public void aSecondSyncMovesNothingSoNothingFlaps() throws Exception {
        MeshRules.Store phone = ownStore(MINE);
        MeshRules.Store tablet = ownStore(MINE);
        phone.add(fix(PHONE, NOW_MS - 60_000, 39.70), NOW_S);
        tablet.add(item("watch", TABLET, NOW_MS - 120_000, new JSONObject().put("steps", 10)), NOW_S);
        sync(phone, tablet, true, MINE, MINE);
        assertEquals(phone.digest(NOW_S), tablet.digest(NOW_S));
        // Again, both ways round: the same copies, so not one item is sent.
        for (int round = 0; round < 3; round++) {
            MeshRules.Session[] s = round % 2 == 0 ? sync(phone, tablet, true, MINE, MINE) : sync(tablet, phone, true, MINE, MINE);
            assertEquals(0, s[0].sent + s[1].sent);
            assertEquals(0, s[0].received + s[1].received);
        }
        assertEquals(MeshRules.digestHash(phone, NOW_S), MeshRules.digestHash(tablet, NOW_S));
        // An older copy offered later never replaces the newer one.
        assertFalse(phone.add(fix(PHONE, NOW_MS - 3_600_000, 1.0), NOW_S));
        assertEquals(39.70, opened(phone, "device:" + PHONE).data.getJSONObject("fix").getDouble("lat"), 1e-9);
    }

    @Test
    public void aTieAtTheSameMillisecondGoesTheSameWayOnEveryDevice() throws Exception {
        long at = NOW_MS - 5_000;
        MeshRules.Item fromPhone = item("watch", PHONE, at, new JSONObject().put("steps", 5000));
        MeshRules.Item fromTablet = item("watch", TABLET, at, new JSONObject().put("steps", 5001));
        assertTrue(fromPhone.version != fromTablet.version);
        String winner = fromPhone.version > fromTablet.version ? PHONE : TABLET;
        for (boolean phoneConnects : new boolean[] {true, false}) {
            MeshRules.Store phone = ownStore(MINE);
            MeshRules.Store tablet = ownStore(MINE);
            phone.add(fromPhone, NOW_S);
            tablet.add(fromTablet, NOW_S);
            if (phoneConnects) sync(phone, tablet, true, MINE, MINE);
            else sync(tablet, phone, true, MINE, MINE);
            assertEquals(winner, opened(phone, "watch").deviceId);
            assertEquals(winner, opened(tablet, "watch").deviceId);
        }
        // The rank is the writer's, keyed: stable, and not the device id in the clear.
        assertEquals(OwnSyncRules.rank(MINE, PHONE), OwnSyncRules.rank(MINE, PHONE));
        assertEquals(at, OwnSyncRules.atOf(fromPhone.version));
    }

    @Test
    public void whoeverConnectsFirstBetweenOwnDevicesIsDecidedByTheTagsNotTheName() {
        // The two share a name, so exactly one of a pair with different tags goes first.
        for (int a = 0; a < 32; a++) {
            for (int b = 0; b < 32; b++) {
                if (a == b) {
                    assertFalse(OwnSyncRules.ownSortsFirst(a, b));
                } else {
                    assertTrue(OwnSyncRules.ownSortsFirst(a, b) ^ OwnSyncRules.ownSortsFirst(b, a));
                }
            }
        }
    }

    @Test
    public void theTwoDevicesTellEachOtherApartAfterConnecting() throws Exception {
        MeshRules.Session[] s = sync(ownStore(MINE), ownStore(MINE), true, MINE, MINE);
        assertEquals(TABLET, s[0].peerDevice);
        assertEquals("Test Tablet", s[0].peerName);
        assertEquals(PHONE, s[1].peerDevice);
        assertEquals("Test Phone", s[1].peerName);
    }

    @Test
    public void aFamilyPhoneNeitherLearnsWhoNorSaysWhoAndTheSyncStillRuns() throws Exception {
        MeshRules.Store phone = ownStore(MINE);
        MeshRules.Store sibling = ownStore(SIBLING);
        phone.add(fix(PHONE, NOW_MS - 60_000, 39.70), NOW_S);
        MeshRules.Session[] s = sync(phone, sibling, true, MINE, SIBLING);
        assertNull(s[0].peerDevice);
        assertNull(s[1].peerDevice);
        // An older phone with no own sync at all ignores the WHO frame too.
        MeshRules.Session[] old = sync(ownStore(MINE), new MeshRules.Store(), true, MINE, null);
        assertNull(old[0].peerDevice);
        // The sibling's phone carries the item, as it carries everything.
        assertEquals(1, s[1].received);
    }

    @Test
    public void ownItemsAreSealedSoAFamilyRelayCarriesThemUnread() throws Exception {
        MeshRules.Store phone = ownStore(MINE);
        MeshRules.Store relay = ownStore(SIBLING);
        MeshRules.Store tablet = ownStore(MINE);
        phone.add(fix(PHONE, NOW_MS - 60_000, 39.70), NOW_S);
        phone.add(item("steps:" + PHONE, PHONE, NOW_MS - 60_000, OwnSyncRules.stepsData(
                Collections.singletonMap(TODAY, new OwnSyncRules.DaySteps(4321, NOW_MS - 60_000, null)))), NOW_S);

        sync(phone, relay, false, MINE, SIBLING);
        assertEquals(2, relay.items.size());
        for (MeshRules.Item i : relay.items.values()) {
            assertNull(OwnSyncRules.open(SIBLING, i));
            assertNull(MeshRules.open(SIBLING, i.env));
            assertNull(MeshRules.open(HOUSEHOLD, i.env));
            assertFalse(OwnSyncRules.isMine(SIBLING, i.id));
            // Nothing in the clear names the person, the device or the kind.
            String wire = i.wire().toString();
            assertFalse(wire.contains(PHONE));
            assertFalse(wire.contains("steps"));
            assertFalse(wire.contains("Test Phone"));
        }
        assertTrue(OwnSyncRules.openAll(SIBLING, relay.items.values()).isEmpty());

        // The relay hands them on to the person's tablet, which opens them.
        sync(relay, tablet, false, SIBLING, MINE);
        assertEquals(4321, OwnSyncRules.parseSteps(opened(tablet, "steps:" + PHONE).data).get(TODAY).steps);
        assertEquals(39.70, opened(tablet, "device:" + PHONE).data.getJSONObject("fix").getDouble("lat"), 1e-9);
    }

    @Test
    public void aCopyWhoseClearVersionWasRaisedOnTheWayIsRefusedByThePersonsDevices() throws Exception {
        MeshRules.Item genuine = fix(PHONE, NOW_MS - 60_000, 39.70);
        JSONObject wire = genuine.wire().put("ver", genuine.version + (1L << 40));
        MeshRules.Item forged = MeshRules.Item.fromWire(wire);
        assertNotNull(forged);
        // A relay cannot tell, so it carries it...
        assertTrue(ownStore(SIBLING).add(forged, NOW_S));
        // ...but the person's device refuses it, so a genuine newer copy still lands afterwards.
        MeshRules.Store tablet = ownStore(MINE);
        assertFalse(tablet.add(forged, NOW_S));
        assertTrue(tablet.add(fix(PHONE, NOW_MS - 1_000, 39.75), NOW_S));
        assertFalse(OwnSyncRules.acceptable(MINE, forged));
        // A copy under someone else's id, or an envelope moved to another id, does not open either.
        MeshRules.Item moved = MeshRules.Item.fromWire(genuine.wire().put("id", OwnSyncRules.itemId(MINE, "watch")));
        assertNull(OwnSyncRules.open(MINE, moved));
    }

    @Test
    public void stepsFollowTheDailyMaximumRuleAndAreNeverDoubleCounted() throws Exception {
        int[] morning = new int[24];
        morning[8] = 1500;
        morning[9] = 1500;
        int[] sameWalk = new int[24];
        sameWalk[8] = 1400;
        sameWalk[9] = 1450;

        Map<String, OwnSyncRules.DaySteps> phone = new TreeMap<>();
        phone.put(TODAY, new OwnSyncRules.DaySteps(3000, NOW_MS, morning));
        Map<String, OwnSyncRules.DaySteps> tablet = new TreeMap<>();
        tablet.put(TODAY, new OwnSyncRules.DaySteps(2850, NOW_MS, sameWalk));
        // Phone and tablet carried on the same walk: counted once, at the higher count per hour.
        assertEquals(3000, OwnSyncRules.dayTotal(Arrays.asList(phone, tablet), TODAY));

        // The tablet's afternoon is a different walk: both count.
        int[] both = sameWalk.clone();
        both[15] = 2500;
        tablet.put(TODAY, new OwnSyncRules.DaySteps(5350, NOW_MS, both));
        assertEquals(5500, OwnSyncRules.dayTotal(Arrays.asList(phone, tablet), TODAY));

        // The watch has no hours: its day total is a floor, never added on top.
        JSONObject watch = new JSONObject().put("day", TODAY).put("steps", 6000).put("read_at", NOW_MS);
        assertEquals(6000, OwnSyncRules.dayTotal(Arrays.asList(phone, tablet, OwnSyncRules.watchDays(watch)), TODAY));
        JSONObject lowWatch = new JSONObject().put("day", TODAY).put("steps", 2000).put("read_at", NOW_MS);
        assertEquals(5500, OwnSyncRules.dayTotal(Arrays.asList(phone, tablet, OwnSyncRules.watchDays(lowWatch)), TODAY));

        // Devices with no hours at all: the highest single device, never the sum.
        Map<String, OwnSyncRules.DaySteps> a = Collections.singletonMap(TODAY, new OwnSyncRules.DaySteps(4000, NOW_MS, null));
        Map<String, OwnSyncRules.DaySteps> b = Collections.singletonMap(TODAY, new OwnSyncRules.DaySteps(3900, NOW_MS, null));
        assertEquals(4000, OwnSyncRules.dayTotal(Arrays.asList(a, b), TODAY));
        // Another day is not touched.
        assertEquals(0, OwnSyncRules.dayTotal(Arrays.asList(a, b), "2027-01-14"));
    }

    @Test
    public void bothDevicesShowTheSameTotalFromTheNewestPerDeviceCounts() throws Exception {
        int[] morning = new int[24];
        morning[7] = 2000;
        int[] evening = new int[24];
        evening[19] = 1200;
        MeshRules.Store phone = ownStore(MINE);
        MeshRules.Store tablet = ownStore(MINE);
        // The tablet still holds an older copy of the phone's count.
        tablet.add(item("steps:" + PHONE, PHONE, NOW_MS - 3_600_000, OwnSyncRules.stepsData(
                Collections.singletonMap(TODAY, new OwnSyncRules.DaySteps(800, NOW_MS - 3_600_000, null)))), NOW_S);
        phone.add(item("steps:" + PHONE, PHONE, NOW_MS - 60_000, OwnSyncRules.stepsData(
                Collections.singletonMap(TODAY, new OwnSyncRules.DaySteps(2000, NOW_MS - 60_000, morning)))), NOW_S);
        tablet.add(item("steps:" + TABLET, TABLET, NOW_MS - 30_000, OwnSyncRules.stepsData(
                Collections.singletonMap(TODAY, new OwnSyncRules.DaySteps(1200, NOW_MS - 30_000, evening)))), NOW_S);
        sync(tablet, phone, true, MINE, MINE);
        assertEquals(3200, total(phone));
        assertEquals(3200, total(tablet));
    }

    private static int total(MeshRules.Store s) {
        List<Map<String, OwnSyncRules.DaySteps>> devices = new java.util.ArrayList<>();
        for (OwnSyncRules.Opened o : OwnSyncRules.openAll(MINE, s.items.values()).values()) {
            if (OwnSyncRules.K_STEPS.equals(o.kind())) devices.add(OwnSyncRules.parseSteps(o.data));
        }
        return OwnSyncRules.dayTotal(devices, TODAY);
    }

    @Test
    public void aDevicesOwnDayOnlyGoesUpAndItsTimeMovesOnlyWithIt() {
        OwnSyncRules.DaySteps d = OwnSyncRules.raise(null, 100, null, 1_000L);
        assertEquals(100, d.steps);
        assertEquals(1_000L, d.at);
        // The same count again (a re-read): unchanged, the same updated-at.
        assertTrue(d == OwnSyncRules.raise(d, 100, null, 2_000L));
        // A lower reading (a counter reset) never lowers the day.
        assertTrue(d == OwnSyncRules.raise(d, 40, null, 3_000L));
        OwnSyncRules.DaySteps up = OwnSyncRules.raise(d, 250, null, 4_000L);
        assertEquals(250, up.steps);
        assertEquals(4_000L, up.at);
    }

    @Test
    public void stepsTravelForTheLastWeekOnly() throws Exception {
        Map<String, OwnSyncRules.DaySteps> days = new TreeMap<>();
        for (int d = 1; d <= 10; d++) days.put(String.format("2027-01-%02d", d), new OwnSyncRules.DaySteps(d * 100, d, null));
        Map<String, OwnSyncRules.DaySteps> back = OwnSyncRules.parseSteps(OwnSyncRules.stepsData(days));
        assertEquals(OwnSyncRules.STEP_DAYS, back.size());
        assertTrue(back.containsKey("2027-01-10"));
        assertFalse(back.containsKey("2027-01-03"));
    }

    @Test
    public void theWatchsStatsBecomeAnItemFiledUnderTheDayTheyWereRead() throws Exception {
        JSONObject stats = new JSONObject("{\"steps\":5234,\"goal\":8000,\"active_min\":31,\"battery\":72,\"volts\":3.95,"
                + "\"uptime\":3600,\"mode\":\"normal\",\"synced\":1800000000,\"charging\":false,\"unplugged\":0,"
                + "\"left_h\":20,\"last_charge_h\":12.5}");
        JSONObject data = OwnSyncRules.watchData(stats, TODAY, NOW_MS, "Jarvis Watch");
        assertEquals(5234, data.getInt("steps"));
        assertEquals(72, data.getInt("battery"));
        assertEquals(TODAY, data.getString("day"));
        assertEquals(5234, OwnSyncRules.watchDays(data).get(TODAY).steps);
    }

    @Test
    public void diagnosticsSayWhatCameFromTheOtherDeviceAndWhatWasAlreadyInSync() throws Exception {
        assertEquals(OwnSyncRules.C_DEVICE, OwnSyncRules.category(MeshRules.T_OWN, OwnSyncRules.K_DEVICE));
        assertEquals(OwnSyncRules.C_STEPS, OwnSyncRules.category(MeshRules.T_OWN, OwnSyncRules.K_STEPS));
        assertEquals(OwnSyncRules.C_FAMILY, OwnSyncRules.category(MeshRules.T_CACHE, null));
        assertEquals(OwnSyncRules.C_CHAT, OwnSyncRules.category(MeshRules.T_CHAT, null));
        assertEquals(OwnSyncRules.C_REQUESTS, OwnSyncRules.category(MeshRules.T_ANSWER, null));
        assertEquals(OwnSyncRules.C_GUARDIAN, OwnSyncRules.category(MeshRules.T_COMMAND, null));

        JSONObject log = new JSONObject();
        OwnSyncRules.recordSync(log, TABLET, "Test Tablet", NOW_MS, new HashSet<>(Arrays.asList("steps", "family")));
        JSONObject row = OwnSyncRules.peers(log).getJSONObject(0);
        assertEquals(TABLET, row.getString("device"));
        assertEquals(NOW_MS, row.getLong("last_sync_at"));
        JSONObject types = row.getJSONObject("types");
        assertEquals("updated", types.getJSONObject("steps").getString("status"));
        assertEquals("Test Tablet", types.getJSONObject("steps").getString("from"));
        assertEquals("in_sync", types.getJSONObject("device").getString("status"));
        assertEquals(OwnSyncRules.CATEGORIES.length, types.length());

        // The next sync brought nothing: every kind is in sync, the first time seen is kept.
        OwnSyncRules.recordSync(log, TABLET, "Test Tablet", NOW_MS + 60_000, new HashSet<>());
        row = OwnSyncRules.peers(log).getJSONObject(0);
        assertEquals("in_sync", row.getJSONObject("types").getJSONObject("steps").getString("status"));
        assertEquals(NOW_MS, row.getLong("first_seen_at"));
    }

    @Test
    public void aSyncReportsWhichItemsItBroughtIn() throws Exception {
        MeshRules.Store phone = ownStore(MINE);
        MeshRules.Store tablet = ownStore(MINE);
        MeshRules.Item f = fix(TABLET, NOW_MS - 10_000, 39.9);
        tablet.add(f, NOW_S);
        MeshRules.Session[] s = sync(phone, tablet, true, MINE, MINE);
        assertEquals(Collections.singletonList(f.id), s[0].receivedIds);
        assertTrue(s[1].receivedIds.isEmpty());
    }
}
