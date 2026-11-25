package com.jarvisos.app;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONObject;
import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collections;
import java.util.List;

public class MeshRulesTest {
    private static final byte[] KEY = "0123456789abcdef0123456789abcdef".getBytes(StandardCharsets.UTF_8);
    private static final byte[] OTHER = "fedcba9876543210fedcba9876543210".getBytes(StandardCharsets.UTF_8);
    private static final long NOW_MS = 1_800_000_000_000L;
    private static final long NOW_S = NOW_MS / 1000;
    private static final long PERIOD = ProximityRules.period(NOW_MS, 900);
    private final SecureRandom random = new SecureRandom();

    private MeshRules.Item message(String text) throws Exception {
        JSONObject p = MeshRules.payload("text", "pat", "abcd1234", new JSONObject().put("text", text), NOW_S);
        MeshRules.Item i = new MeshRules.Item(MeshRules.newId(random), NOW_S + MeshRules.TTL_S, 0,
                MeshRules.seal(KEY, p, random));
        i.mine = true;
        return i;
    }

    @Test
    public void theEnvelopeIsThePrivateChatsAndOpensOnlyWithTheKey() throws Exception {
        JSONObject p = MeshRules.payload("text", "pat", "abcd1234", new JSONObject().put("text", "Running late"), NOW_S);
        JSONObject env = MeshRules.seal(KEY, p, random);
        assertEquals("sealed", env.getString("kind"));
        assertEquals(12, Base64.getDecoder().decode(env.getString("iv")).length);
        assertEquals("Running late", MeshRules.open(KEY, env).getString("text"));
        assertNull(MeshRules.open(OTHER, env));
        // The private chat's own opener reads the same envelope.
        String raw = PrivateEnvelope.FENCE + "\n" + env + "\n" + PrivateEnvelope.FENCE;
        assertEquals("Running late", PrivateEnvelope.previewText(raw, KEY));
        // Two seals of the same words never look alike (fresh IV).
        assertFalse(env.getString("ct").equals(MeshRules.seal(KEY, p, random).getString("ct")));
    }

    @Test
    public void payloadsAreCheckedAndSmall() throws Exception {
        assertEquals(39.7, MeshRules.payload("pin", "pat", "r", new JSONObject().put("lat", 39.7).put("lon", -104.9)
                .put("label", "Trailhead"), NOW_S).getDouble("lat"), 0.0001);
        String small = Base64.getEncoder().encodeToString(new byte[1000]);
        assertEquals("image/jpeg", MeshRules.payload("thumb", "pat", "r", new JSONObject().put("data", small), NOW_S).getString("mime"));
        String big = Base64.getEncoder().encodeToString(new byte[MeshRules.THUMB_MAX + 1]);
        expectRefused("thumb", new JSONObject().put("data", big));
        expectRefused("text", new JSONObject().put("text", "  "));
        char[] longText = new char[MeshRules.TEXT_MAX + 1];
        Arrays.fill(longText, 'a');
        expectRefused("text", new JSONObject().put("text", new String(longText)));
        expectRefused("pin", new JSONObject().put("lat", 91).put("lon", 0));
        expectRefused("video", new JSONObject());
    }

    private void expectRefused(String kind, JSONObject fields) throws Exception {
        try {
            MeshRules.payload(kind, "pat", "r", fields, NOW_S);
            fail("accepted " + kind);
        } catch (IllegalArgumentException expected) {
            // refused, with a reason
        }
    }

    @Test
    public void theStoreKeepsEachMessageOnceAndDropsWhatExpiredOrTravelledTooFar() throws Exception {
        MeshRules.Store s = new MeshRules.Store();
        MeshRules.Item m = message("hi");
        assertTrue(s.add(m, NOW_S));
        assertFalse(s.add(m, NOW_S));
        assertTrue(s.receive(m.wire().put("id", MeshRules.newId(random)), NOW_S, NOW_MS));
        assertFalse(s.receive(m.wire(), NOW_S, NOW_MS)); // same id again
        assertFalse(s.add(new MeshRules.Item(MeshRules.newId(random), NOW_S - 1, 0, m.env), NOW_S));
        assertFalse(s.add(new MeshRules.Item(MeshRules.newId(random), NOW_S + 60, MeshRules.HOP_LIMIT + 1, m.env), NOW_S));
        assertFalse(s.receive(new JSONObject().put("id", "nope"), NOW_S, NOW_MS));
        assertEquals(2, s.items.size());
        // A message at the hop limit is kept but not passed on.
        MeshRules.Item last = new MeshRules.Item(MeshRules.newId(random), NOW_S + 60, MeshRules.HOP_LIMIT, m.env);
        s.add(last, NOW_S);
        assertFalse(s.missingFrom(Collections.emptyMap(), NOW_S).contains(last));
    }

    @Test
    public void theCapDropsOthersBeforeThisPhonesUnsentMessages() throws Exception {
        MeshRules.Store s = new MeshRules.Store();
        MeshRules.Item mine = message("mine");
        s.add(mine, NOW_S);
        for (int k = 0; k < MeshRules.STORE_MAX + 5; k++) {
            MeshRules.Item other = new MeshRules.Item(MeshRules.newId(random), NOW_S + 600 + k, 1, mine.env);
            s.add(other, NOW_S);
        }
        assertEquals(MeshRules.STORE_MAX, s.items.size());
        assertTrue(s.items.containsKey(mine.id));
    }

    @Test
    public void storageAndSyncStateSurvive() throws Exception {
        MeshRules.Store s = new MeshRules.Store();
        MeshRules.Item m = message("hi");
        s.add(m, NOW_S);
        assertEquals(1, s.unsyncedMine().size());
        s.markSynced(Collections.singletonList(m.id));
        MeshRules.Store back = MeshRules.Store.decode(s.encode());
        assertTrue(back.items.get(m.id).synced);
        assertTrue(back.unsyncedMine().isEmpty());
        assertTrue(MeshRules.Store.decode("{broken").items.isEmpty());
    }

    @Test
    public void framesAreCutAndJoined() {
        byte[] body = new byte[1000];
        random.nextBytes(body);
        List<byte[]> pieces = MeshRules.frames(MeshRules.F_ITEM, body, 20);
        assertTrue(pieces.size() > 50);
        MeshRules.Reassembler r = new MeshRules.Reassembler();
        Object[] whole = null;
        for (byte[] p : pieces) {
            assertTrue(p.length <= 20);
            whole = r.add(p);
        }
        assertNotNull(whole);
        assertArrayEquals(body, (byte[]) whole[1]);
        assertEquals(1, MeshRules.frames(MeshRules.F_END, new byte[0], 20).size());
    }

    /** Two phones' sessions joined back to back, as a GATT client and server would be. */
    private static void sync(MeshRules.Session client, MeshRules.Session server) throws Exception {
        for (byte[] p : client.clientOpening()) assertTrue(server.serverWrite(p));
        List<byte[]> back = Collections.emptyList();
        byte[] piece;
        while ((piece = server.serverNextRead()) != null) {
            List<byte[]> answer = client.clientRead(piece);
            if (!answer.isEmpty()) back = answer;
        }
        for (byte[] p : back) assertTrue(server.serverWrite(p));
    }

    @Test
    public void aSyncLeavesBothPhonesWithEverythingAndNothingTwice() throws Exception {
        MeshRules.Store a = new MeshRules.Store();
        MeshRules.Store b = new MeshRules.Store();
        MeshRules.Item shared = message("both have this");
        a.add(shared, NOW_S);
        b.receive(shared.wire(), NOW_S, NOW_MS);
        a.add(message("only on A"), NOW_S);
        b.add(message("only on B"), NOW_S);
        b.add(message("also only on B"), NOW_S);

        MeshRules.Session client = new MeshRules.Session(a, KEY, PERIOD, 20, NOW_MS);
        MeshRules.Session server = new MeshRules.Session(b, KEY, PERIOD, 20, NOW_MS);
        sync(client, server);
        assertTrue(client.done && server.done);
        assertEquals(4, a.items.size());
        assertEquals(4, b.items.size());
        assertEquals(2, client.received);
        assertEquals(1, server.received);
        assertEquals(a.items.keySet(), b.items.keySet());

        // Again: nothing moves.
        MeshRules.Session again = new MeshRules.Session(a, KEY, PERIOD, 180, NOW_MS);
        MeshRules.Session againServer = new MeshRules.Session(b, KEY, PERIOD, 180, NOW_MS);
        sync(again, againServer);
        assertEquals(0, again.received + againServer.received);
    }

    @Test
    public void aMessageHopsAcrossThreePhones() throws Exception {
        MeshRules.Store a = new MeshRules.Store();
        MeshRules.Store b = new MeshRules.Store();
        MeshRules.Store c = new MeshRules.Store();
        MeshRules.Item m = message("pass it on");
        a.add(m, NOW_S);
        sync(new MeshRules.Session(b, KEY, PERIOD, 180, NOW_MS), new MeshRules.Session(a, KEY, PERIOD, 180, NOW_MS));
        sync(new MeshRules.Session(c, KEY, PERIOD, 180, NOW_MS), new MeshRules.Session(b, KEY, PERIOD, 180, NOW_MS));
        assertEquals(2, c.items.get(m.id).hops);
        assertFalse(c.items.get(m.id).mine);
        assertEquals("pass it on", MeshRules.open(KEY, c.items.get(m.id).env).getString("text"));
    }

    @Test
    public void aPhoneWithoutTheKeyIsTurnedAway() throws Exception {
        MeshRules.Store b = new MeshRules.Store();
        b.add(message("secret"), NOW_S);
        MeshRules.Session stranger = new MeshRules.Session(new MeshRules.Store(), OTHER, PERIOD, 180, NOW_MS);
        MeshRules.Session server = new MeshRules.Session(b, KEY, PERIOD, 180, NOW_MS);
        List<byte[]> opening = stranger.clientOpening();
        assertFalse(server.serverWrite(opening.get(0)));
        assertNull(server.serverNextRead());
        // A clock a period off still works.
        assertTrue(MeshRules.helloOk(KEY, PERIOD, MeshRules.hello(KEY, PERIOD + 1)));
        assertFalse(MeshRules.helloOk(KEY, PERIOD, MeshRules.hello(KEY, PERIOD + 3)));
    }

    @Test
    public void connectionsHappenOnlyWhenThereIsSomethingToPassOn() {
        long longAgo = Long.MAX_VALUE;
        // Nothing to pass on either way: no connection.
        assertFalse(MeshRules.shouldConnect(true, false, false, longAgo, true, true, true, true));
        // This phone has new messages: connect (it sorts first).
        assertTrue(MeshRules.shouldConnect(true, false, true, longAgo, true, false, true, true));
        // ...but not to a phone that accepts no connections, nor right after a sync.
        assertFalse(MeshRules.shouldConnect(false, true, true, longAgo, true, true, true, true));
        assertFalse(MeshRules.shouldConnect(true, true, true, 5_000L, true, true, true, true));
        // Both could connect: the one sorting second waits, unless it cannot serve or the first did not.
        assertFalse(MeshRules.shouldConnect(true, false, true, 90_000L, true, false, false, true));
        assertTrue(MeshRules.shouldConnect(true, false, true, 90_000L, true, false, false, false));
        assertTrue(MeshRules.shouldConnect(true, false, true, MeshRules.FALLBACK_MS, true, false, false, true));
        // Unchanged on both sides since the last sync: only after a while.
        assertFalse(MeshRules.shouldConnect(true, true, true, 5 * 60_000L, false, false, true, true));
        assertTrue(MeshRules.shouldConnect(true, true, false, MeshRules.RESYNC_MS, false, false, true, true));
        // A scan-only tablet pulls as soon as the other phone's tag changes.
        assertTrue(MeshRules.shouldConnect(true, true, false, 90_000L, false, true, false, false));
    }

    @Test
    public void theMailTagRidesInTheFlagByte() {
        int flags = ProximityRules.FLAG_CAN_CONNECT | ProximityRules.FLAG_HAS_MAIL | ProximityRules.mailTagBits(0x3FL);
        assertEquals(0x1F, ProximityRules.mailTag(flags));
        assertTrue((flags & ProximityRules.FLAG_CAN_CONNECT) != 0);
        assertTrue(flags <= 0xff);
        assertEquals(0, ProximityRules.mailTag(ProximityRules.FLAG_MOVING));
    }

    @Test
    public void hasMailAndPostedIds() throws Exception {
        MeshRules.Store s = new MeshRules.Store();
        assertFalse(MeshRules.hasMail(s, NOW_S));
        long before = MeshRules.digestHash(s, NOW_S);
        s.add(message("x"), NOW_S);
        assertTrue(MeshRules.hasMail(s, NOW_S));
        assertFalse(before == MeshRules.digestHash(s, NOW_S));
        JSONObject answer = new JSONObject("{\"results\":{\"b\":\"posted\",\"a\":\"posted\",\"c\":\"failed\"}}");
        assertEquals(Arrays.asList("a", "b"), MeshRules.postedIds(answer));
        assertTrue(MeshRules.postedIds(null).isEmpty());
    }

    @Test
    public void theConfigCarriesTheChatKey() throws Exception {
        String key = Base64.getEncoder().encodeToString(KEY);
        ProximityRules.Config c = ProximityRules.parseConfig(new JSONObject().put("key", key).put("chat_key", key).put("me", "pat"));
        assertArrayEquals(KEY, c.chatKey);
        assertNull(ProximityRules.parseConfig(new JSONObject().put("key", key)).chatKey);
    }

    // -- rooms: a key per room, every phone relays, only members open ----------------------

    private static final byte[] ROOM_KEY = new byte[32];
    private static final byte[] GROUP_KEY = new byte[32];
    static {
        for (int k = 0; k < 32; k++) {
            ROOM_KEY[k] = (byte) k;
            GROUP_KEY[k] = (byte) (200 - k);
        }
    }

    private static MeshRules.Room room(String token, byte[] key, byte[]... previous) {
        return new MeshRules.Room(token, token, 1, key, Arrays.asList(previous));
    }

    private MeshRules.Item roomText(MeshRules.Room r, String from, String text) throws Exception {
        JSONObject p = MeshRules.payload("text", from, r.room, new JSONObject().put("text", text), NOW_S);
        return MeshRules.roomMessage(r, p, NOW_S, random);
    }

    @Test
    public void theRoutingTagMatchesTheServers() {
        // services/gateway/tests/test_family_mesh.py pins the same vector (key bytes 0..31).
        assertEquals("1349794a53c68320fb8f9f158d3c27df", MeshRules.routeTag(ROOM_KEY, 100, repeat('0', 32)));
    }

    private static String repeat(char c, int n) {
        char[] a = new char[n];
        Arrays.fill(a, c);
        return new String(a);
    }

    @Test
    public void aRoomMessageIsSealedWithItsRoomsKeyAndOpensOnlyForItsMembers() throws Exception {
        MeshRules.Room oneToOne = room("abcd1234", ROOM_KEY);
        MeshRules.Room group = room("grp98765", GROUP_KEY);
        MeshRules.Item m = roomText(oneToOne, "pat", "Meet at the car");
        assertTrue(m.mine);
        assertEquals("abcd1234", m.room);
        // Not the household key, not the other room's key.
        assertNull(MeshRules.open(KEY, m.env));
        assertNull(MeshRules.open(GROUP_KEY, m.env));
        assertEquals("Meet at the car", MeshRules.open(ROOM_KEY, m.env).getString("text"));
        // A member of the room opens it; someone in the group only does not.
        assertEquals("Meet at the car", MeshRules.openChat(m, Arrays.asList(group, oneToOne), KEY).getString("text"));
        assertNull(MeshRules.openChat(m, Collections.singletonList(group), KEY));
        assertNull(MeshRules.openChat(m, Collections.emptyList(), KEY));
        // The private chat's own opener reads the same envelope.
        String raw = PrivateEnvelope.FENCE + "\n" + m.env + "\n" + PrivateEnvelope.FENCE;
        assertEquals("Meet at the car", PrivateEnvelope.previewText(raw, ROOM_KEY));
    }

    @Test
    public void aNonMemberRelayCannotOpenButStillForwards() throws Exception {
        MeshRules.Room group = room("grp98765", GROUP_KEY);
        MeshRules.Store author = new MeshRules.Store();
        MeshRules.Store relay = new MeshRules.Store();
        MeshRules.Store member = new MeshRules.Store();
        MeshRules.Item m = roomText(group, "pat", "Lost signal, all fine");
        author.add(m, NOW_S);
        // Every family phone shares the household proximity key, so the relay syncs with both.
        sync(new MeshRules.Session(relay, KEY, PERIOD, 180, NOW_MS), new MeshRules.Session(author, KEY, PERIOD, 180, NOW_MS));
        MeshRules.Item carried = relay.items.get(m.id);
        assertNotNull(carried);
        assertNull(MeshRules.openChat(carried, Collections.singletonList(room("abcd1234", ROOM_KEY)), KEY));
        // What the relay sees in the clear: the id, the expiry, the hops and the tag; never the room.
        String wire = carried.wire().toString();
        assertFalse(wire.contains("grp98765"));
        assertFalse(wire.contains("Lost signal"));
        assertFalse(wire.contains("pat"));
        // A peer cannot plant a room or "mine" on a copy it hands over.
        assertNull(carried.room);
        assertFalse(carried.mine);

        sync(new MeshRules.Session(member, KEY, PERIOD, 180, NOW_MS), new MeshRules.Session(relay, KEY, PERIOD, 180, NOW_MS));
        MeshRules.Item arrived = member.items.get(m.id);
        assertEquals(2, arrived.hops);
        assertEquals("Lost signal, all fine",
                MeshRules.openChat(arrived, Collections.singletonList(group), KEY).getString("text"));
    }

    @Test
    public void twoMembersExchangeInBothDirections() throws Exception {
        MeshRules.Room shared = room("abcd1234", ROOM_KEY);
        MeshRules.Store a = new MeshRules.Store();
        MeshRules.Store b = new MeshRules.Store();
        MeshRules.Item fromA = roomText(shared, "pat", "Where are you?");
        MeshRules.Item fromB = roomText(shared, "alex", "At the lookout");
        a.add(fromA, NOW_S);
        b.add(fromB, NOW_S);
        sync(new MeshRules.Session(a, KEY, PERIOD, 20, NOW_MS), new MeshRules.Session(b, KEY, PERIOD, 20, NOW_MS));
        List<MeshRules.Room> rooms = Collections.singletonList(shared);
        assertEquals("At the lookout", MeshRules.openChat(a.items.get(fromB.id), rooms, null).getString("text"));
        assertEquals("Where are you?", MeshRules.openChat(b.items.get(fromA.id), rooms, null).getString("text"));
        assertEquals("abcd1234", MeshRules.openChat(b.items.get(fromA.id), rooms, null).getString("room"));
    }

    @Test
    public void theRoutingTagRevealsNothingAcrossPeriodsOrMessages() throws Exception {
        String id = MeshRules.newId(random);
        long p = MeshRules.tagPeriod(NOW_S);
        String tag = MeshRules.routeTag(ROOM_KEY, p, id);
        assertEquals(32, tag.length());
        // The same room in the next period, or another message in the same period: unrelated tags.
        assertFalse(tag.equals(MeshRules.routeTag(ROOM_KEY, p + 1, id)));
        assertFalse(tag.equals(MeshRules.routeTag(ROOM_KEY, p, MeshRules.newId(random))));
        // Two rooms never share a tag, and the tag is not the key or its id.
        assertFalse(tag.equals(MeshRules.routeTag(GROUP_KEY, p, id)));
        assertFalse(tag.contains(ProximityRules.hex(ROOM_KEY, 8)));
        // Two messages to one room look as unrelated as two messages to two rooms.
        MeshRules.Room r = room("abcd1234", ROOM_KEY);
        MeshRules.Item a = roomText(r, "pat", "one");
        MeshRules.Item b = roomText(r, "pat", "two");
        assertFalse(a.routeTag().equals(b.routeTag()));
        // A message a period later is still found by its member (the period comes from its expiry).
        long later = NOW_S + MeshRules.ROOM_TAG_PERIOD_S;
        MeshRules.Item next = MeshRules.roomMessage(r,
                MeshRules.payload("text", "pat", r.room, new JSONObject().put("text", "later"), later), later, random);
        assertEquals("later", MeshRules.openChat(next, Collections.singletonList(r), null).getString("text"));
    }

    @Test
    public void aRotatedKeyStillOpensWhatWasSealedBeforeForAMemberWhoHadIt() throws Exception {
        MeshRules.Room before = room("grp98765", GROUP_KEY);
        MeshRules.Item old = roomText(before, "pat", "Sent before the change");
        MeshRules.Room stayer = room("grp98765", ROOM_KEY, GROUP_KEY);
        MeshRules.Room newcomer = room("grp98765", ROOM_KEY);
        assertEquals("Sent before the change",
                MeshRules.openChat(old, Collections.singletonList(stayer), null).getString("text"));
        assertNull(MeshRules.openChat(old, Collections.singletonList(newcomer), null));
    }

    @Test
    public void aMessageHeldFromBeforeRoomKeysStillOpensWithTheHouseholdKey() throws Exception {
        MeshRules.Item legacy = message("from an older phone");
        assertNull(legacy.routeTag());
        assertEquals("from an older phone",
                MeshRules.openChat(legacy, Collections.singletonList(room("abcd1234", ROOM_KEY)), KEY).getString("text"));
        assertNull(MeshRules.openChat(legacy, Collections.emptyList(), null));
    }

    @Test
    public void aTagForAnotherRoomOrAWrongRoomNameIsNotOpened() throws Exception {
        MeshRules.Room real = room("abcd1234", ROOM_KEY);
        MeshRules.Item m = roomText(real, "pat", "hi");
        // The key matches but the room list names it as a different room: not shown there.
        assertNull(MeshRules.openChat(m, Collections.singletonList(room("zzzz9999", ROOM_KEY)), null));
    }

    @Test
    public void ownMessagesKeepTheirRoomOnThisPhoneOnly() throws Exception {
        MeshRules.Store s = new MeshRules.Store();
        MeshRules.Item m = roomText(room("abcd1234", ROOM_KEY), "pat", "hi");
        s.add(m, NOW_S);
        assertFalse(m.wire().has("room"));
        assertEquals("abcd1234", MeshRules.Store.decode(s.encode()).items.get(m.id).room);
    }

    @Test
    public void roomsAreReadFromTheServersAnswerAndShownWithoutKeys() throws Exception {
        String key = Base64.getEncoder().encodeToString(ROOM_KEY);
        String old = Base64.getEncoder().encodeToString(GROUP_KEY);
        JSONObject answer = new JSONObject("{\"rooms\":[{\"room\":\"abcd1234\",\"name\":\"Alex\",\"type\":1,\"key\":\""
                + key + "\",\"previous\":[\"" + old + "\",\"short\"]},{\"room\":\"bad room\",\"key\":\"" + key
                + "\"},{\"room\":\"nokey123\",\"key\":\"abc\"}]}");
        List<MeshRules.Room> rooms = MeshRules.parseRooms(answer);
        assertEquals(1, rooms.size());
        assertEquals("Alex", rooms.get(0).name);
        assertEquals(1, rooms.get(0).previous.size());
        String shown = MeshRules.roomsForApp(rooms).toString();
        assertTrue(shown.contains("abcd1234"));
        assertFalse(shown.contains(key));
        assertTrue(MeshRules.parseRooms(null).isEmpty());
    }
}
