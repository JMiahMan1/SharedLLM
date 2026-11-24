package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.SecureRandom;
import java.security.spec.ECGenParameterSpec;
import java.util.Base64;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

public class OfflineRulesTest {
    private static final String PAT_KEY = "pat-api-key-0123456789";
    private static final String ALEX_KEY = "alex-api-key-9876543210";
    private static final long NOW_S = 1_800_000_000L;
    private final SecureRandom random = new SecureRandom();

    private static KeyPair ec() throws Exception {
        KeyPairGenerator g = KeyPairGenerator.getInstance("EC");
        g.initialize(new ECGenParameterSpec("secp256r1"));
        return g.generateKeyPair();
    }

    private static String pub(KeyPair k) {
        return Base64.getEncoder().encodeToString(k.getPublic().getEncoded());
    }

    // -- relayed requests ------------------------------------------------------------

    @Test
    public void aRequestIsSignedAndSealedWithTheSendersOwnKey() throws Exception {
        JSONObject r = OfflineRules.request(PAT_KEY, MeshRules.newId(random), "pat", "ask",
                new JSONObject().put("text", "Is the garage shut?"), NOW_S, random);
        assertTrue(OfflineRules.requestSignedBy(PAT_KEY, r));
        assertFalse(OfflineRules.requestSignedBy(ALEX_KEY, r));
        assertEquals(NOW_S + 3600, r.getLong("expires_at"));
        // The relay cannot read it; the sender's seal key opens it.
        assertNull(MeshRules.open(OfflineRules.deriveKeys(ALEX_KEY)[1], r.getJSONObject("env")));
        assertEquals("Is the garage shut?", MeshRules.open(OfflineRules.deriveKeys(PAT_KEY)[1], r.getJSONObject("env")).getString("text"));
    }

    @Test
    public void changingAnyPartOfARequestBreaksItsSignature() throws Exception {
        JSONObject r = OfflineRules.request(PAT_KEY, MeshRules.newId(random), "pat", "command",
                new JSONObject().put("text", "lights off"), NOW_S, random);
        for (String[] change : new String[][] {{"from", "alex"}, {"kind", "ask"}, {"id", "0".repeat(32)}}) {
            JSONObject copy = new JSONObject(r.toString()).put(change[0], change[1]);
            assertFalse(change[0], OfflineRules.requestSignedBy(PAT_KEY, copy));
        }
        JSONObject later = new JSONObject(r.toString()).put("expires_at", r.getLong("expires_at") + 60);
        assertFalse(OfflineRules.requestSignedBy(PAT_KEY, later));
    }

    @Test
    public void lightCommandsExpireSoonQuestionsInAnHourEditsInADay() {
        assertEquals(600, OfflineRules.lifetimeS("command"));
        assertEquals(3600, OfflineRules.lifetimeS("ask"));
        assertEquals(86400, OfflineRules.lifetimeS("shopping_add"));
        assertEquals(0, OfflineRules.lifetimeS("open_the_safe"));
    }

    @Test
    public void aQueuedRequestAlwaysSaysWhereItIs() {
        assertEquals("no_family_phone_nearby", OfflineRules.requestStatus(false, false, false, false));
        assertEquals("waiting_for_online_phone", OfflineRules.requestStatus(false, false, true, false));
        assertEquals("expired", OfflineRules.requestStatus(false, true, true, false));
        assertEquals("answered", OfflineRules.requestStatus(true, true, false, false));
    }

    @Test
    public void editsAreIdempotentByIdWhicheverPhonesCarryThem() throws Exception {
        MeshRules.Store a = new MeshRules.Store();
        JSONObject r = OfflineRules.request(PAT_KEY, MeshRules.newId(random), "pat", "shopping_add",
                new JSONObject().put("item", "Milk"), NOW_S, random);
        MeshRules.Item item = new MeshRules.Item(r.getString("id"), r.getLong("expires_at"), 0, r.getJSONObject("env"));
        item.type = MeshRules.T_REQUEST;
        item.meta = r;
        assertTrue(a.add(item, NOW_S));
        assertFalse(a.receive(item.wire(), NOW_S, 0)); // the same edit back from a peer: once
        assertEquals(1, a.items.size());
    }

    // -- the cache: digests, newest wins -----------------------------------------------

    private MeshRules.Item cached(String to, String key, long version) throws Exception {
        MeshRules.Item i = new MeshRules.Item(OfflineRules.cacheId(to, key), NOW_S + 86400, 0,
                MeshRules.seal(OfflineRules.deriveKeys(PAT_KEY)[1], new JSONObject().put("v", version), random));
        i.type = MeshRules.T_CACHE;
        i.version = version;
        i.meta = new JSONObject().put("to", to).put("key", key);
        return i;
    }

    @Test
    public void theNewestServerVersionWinsWhoeverBringsIt() throws Exception {
        MeshRules.Store s = new MeshRules.Store();
        assertTrue(s.add(cached("pat", "shopping", 100), NOW_S));
        assertFalse(s.add(cached("pat", "shopping", 90), NOW_S));
        assertFalse(s.add(cached("pat", "shopping", 100), NOW_S));
        assertTrue(s.add(cached("pat", "shopping", 200), NOW_S));
        assertEquals(1, s.items.size());
        assertEquals(200, s.items.get(OfflineRules.cacheId("pat", "shopping")).version);
    }

    @Test
    public void theDigestOffersOnlyWhatIsNewer() throws Exception {
        MeshRules.Store fresh = new MeshRules.Store();
        MeshRules.Store stale = new MeshRules.Store();
        fresh.add(cached("pat", "shopping", 200), NOW_S);
        fresh.add(cached("pat", "calendar", 50), NOW_S);
        stale.add(cached("pat", "shopping", 100), NOW_S);
        stale.add(cached("pat", "calendar", 50), NOW_S);
        Map<String, Long> staleDigest = MeshRules.Store.parseDigest(stale.digest(NOW_S));
        List<MeshRules.Item> give = fresh.missingFrom(staleDigest, NOW_S);
        assertEquals(1, give.size());
        assertEquals(200, give.get(0).version);
        // And nothing back the other way.
        assertTrue(stale.missingFrom(MeshRules.Store.parseDigest(fresh.digest(NOW_S)), NOW_S).isEmpty());
    }

    @Test
    public void theFreshestPhoneUpdatesTheRestHopByHop() throws Exception {
        MeshRules.Store a = new MeshRules.Store();
        MeshRules.Store b = new MeshRules.Store();
        MeshRules.Store c = new MeshRules.Store();
        a.add(cached("gracie", "locations", 300), NOW_S);
        b.add(cached("gracie", "locations", 100), NOW_S);
        c.add(cached("gracie", "locations", 200), NOW_S);
        long period = 7;
        byte[] key = "0123456789abcdef0123456789abcdef".getBytes();
        syncPair(b, a, key, period);
        syncPair(c, b, key, period);
        assertEquals(300, c.items.get(OfflineRules.cacheId("gracie", "locations")).version);
        // A cached item is passed on however many phones it has been through.
        assertEquals(0, c.items.get(OfflineRules.cacheId("gracie", "locations")).hops);
    }

    private static void syncPair(MeshRules.Store client, MeshRules.Store server, byte[] key, long period) throws Exception {
        MeshRules.Session cs = new MeshRules.Session(client, key, period, 180, NOW_S * 1000);
        MeshRules.Session ss = new MeshRules.Session(server, key, period, 180, NOW_S * 1000);
        for (byte[] p : cs.clientOpening()) assertTrue(ss.serverWrite(p));
        List<byte[]> back = Collections.emptyList();
        byte[] piece;
        while ((piece = ss.serverNextRead()) != null) {
            List<byte[]> answer = cs.clientRead(piece);
            if (!answer.isEmpty()) back = answer;
        }
        for (byte[] p : back) assertTrue(ss.serverWrite(p));
    }

    @Test
    public void aCachedItemIsReadableOnlyByWhoItIsFor() throws Exception {
        // The server seals each member's items with that member's key, after its sharing rules.
        JSONObject inner = new JSONObject().put("to", "gracie").put("data", "x");
        JSONObject env = MeshRules.seal(OfflineRules.deriveKeys("gracie-key")[1], inner, random);
        assertNull(MeshRules.open(OfflineRules.deriveKeys(PAT_KEY)[1], env));
        assertEquals("gracie", MeshRules.open(OfflineRules.deriveKeys("gracie-key")[1], env).getString("to"));
        assertNotEquals(OfflineRules.cacheId("pat", "locations"), OfflineRules.cacheId("gracie", "locations"));
    }

    // -- parent commands ---------------------------------------------------------------------

    private OfflineRules.ChildGuard guardFor(KeyPair mom) throws Exception {
        OfflineRules.ChildGuard g = new OfflineRules.ChildGuard();
        g.setKeys(new JSONObject("{\"parents\":[{\"user\":\"mom\",\"device\":\"mom-phone\",\"pub\":\"" + pub(mom) + "\"}],\"version\":\"v1\"}"));
        return g;
    }

    @Test
    public void aParentsCommandIsAcceptedOnceAndAnOldUnlockReplayedIsRefused() throws Exception {
        KeyPair mom = ec();
        OfflineRules.ChildGuard g = guardFor(mom);
        JSONObject unlock = OfflineRules.command(mom.getPrivate(), MeshRules.newId(random), "mom", "mom-phone", "kid",
                "unlock", 0, NOW_S, 1800, 1);
        JSONObject lock = OfflineRules.command(mom.getPrivate(), MeshRules.newId(random), "mom", "mom-phone", "kid",
                "lock", 0, NOW_S + 60, 1800, 2);
        assertEquals(OfflineRules.Verdict.OK, g.check(unlock, "kid", NOW_S));
        g.accept(unlock);
        assertEquals(OfflineRules.Verdict.OK, g.check(lock, "kid", NOW_S + 60));
        g.accept(lock);
        // The child replays the earlier unlock: refused (seen, and older than the lock's sequence).
        assertEquals(OfflineRules.Verdict.ALREADY_SEEN, g.check(unlock, "kid", NOW_S + 120));
        // Even with its memory of ids gone, the sequence still refuses it.
        g.seen.clear();
        assertEquals(OfflineRules.Verdict.OUT_OF_ORDER, g.check(unlock, "kid", NOW_S + 120));
        // And the state survives a restart.
        OfflineRules.ChildGuard back = OfflineRules.ChildGuard.fromJson(g.toJson());
        assertEquals(OfflineRules.Verdict.OUT_OF_ORDER, back.check(unlock, "kid", NOW_S + 120));
    }

    @Test
    public void aChildCannotForgeOrAlterAParentsCommand() throws Exception {
        KeyPair mom = ec();
        KeyPair kid = ec();
        OfflineRules.ChildGuard g = guardFor(mom);
        JSONObject forged = OfflineRules.command(kid.getPrivate(), MeshRules.newId(random), "mom", "mom-phone", "kid",
                "unlock", 0, NOW_S, 1800, 9);
        assertEquals(OfflineRules.Verdict.BAD_SIGNATURE, g.check(forged, "kid", NOW_S));
        JSONObject lock = OfflineRules.command(mom.getPrivate(), MeshRules.newId(random), "mom", "mom-phone", "kid",
                "lock", 30, NOW_S, 1800, 3);
        JSONObject altered = new JSONObject(lock.toString()).put("action", "unlock");
        assertEquals(OfflineRules.Verdict.BAD_SIGNATURE, g.check(altered, "kid", NOW_S));
        JSONObject bumped = new JSONObject(lock.toString()).put("seq", 99);
        assertEquals(OfflineRules.Verdict.BAD_SIGNATURE, g.check(bumped, "kid", NOW_S));
        JSONObject stranger = OfflineRules.command(kid.getPrivate(), MeshRules.newId(random), "uncle", "uncle-phone",
                "kid", "unlock", 0, NOW_S, 1800, 1);
        assertEquals(OfflineRules.Verdict.UNKNOWN_SIGNER, g.check(stranger, "kid", NOW_S));
        assertEquals(OfflineRules.Verdict.NOT_FOR_ME, g.check(lock, "sibling", NOW_S));
    }

    @Test
    public void expiredAndFutureCommandsAreRefused() throws Exception {
        KeyPair mom = ec();
        OfflineRules.ChildGuard g = guardFor(mom);
        JSONObject c = OfflineRules.command(mom.getPrivate(), MeshRules.newId(random), "mom", "mom-phone", "kid",
                "lock", 0, NOW_S, 600, 1);
        assertEquals(OfflineRules.Verdict.EXPIRED, g.check(c, "kid", NOW_S + 601));
        assertEquals(OfflineRules.Verdict.FROM_THE_FUTURE, g.check(c, "kid", NOW_S - 3600));
    }

    @Test
    public void theChildsAcknowledgementIsSignedByTheChild() throws Exception {
        KeyPair kid = ec();
        KeyPair other = ec();
        JSONObject a = OfflineRules.ack(kid.getPrivate(), "a".repeat(32), "kid", "kid-phone", NOW_S, "applied");
        assertTrue(OfflineRules.ecVerify(pub(kid), OfflineRules.ackBytes(a), a.getString("sig")));
        assertFalse(OfflineRules.ecVerify(pub(other), OfflineRules.ackBytes(a), a.getString("sig")));
    }

    @Test
    public void theMostRecentParentDecisionWins() throws Exception {
        long nowMs = NOW_S * 1000;
        OfflineRules.Overlay o = new OfflineRules.Overlay();
        o.apply(new JSONObject().put("action", "lock").put("arg", 0).put("issued_at", NOW_S), nowMs);
        assertTrue(o.locked(nowMs));
        o.apply(new JSONObject().put("action", "unlock").put("issued_at", NOW_S + 60), nowMs);
        assertFalse(o.locked(nowMs));
        // An older lock arriving late does not undo the newer unlock.
        o.apply(new JSONObject().put("action", "lock").put("arg", 0).put("issued_at", NOW_S + 30), nowMs);
        assertFalse(o.locked(nowMs));
        o.apply(new JSONObject().put("action", "lock").put("arg", 30).put("issued_at", NOW_S + 120), nowMs);
        assertTrue(o.locked(nowMs + 60_000));
        assertFalse(o.locked(NOW_S * 1000 + 151 * 60_000L));
        o.apply(new JSONObject().put("action", "bonus").put("arg", 15).put("issued_at", NOW_S + 130), nowMs);
        assertEquals(nowMs + 15 * 60_000L, o.unlockedUntilMs);
    }

    @Test
    public void bluetoothOffIsLoggedWithWhenItStartedAndEnded() {
        OfflineRules.OffLog log = new OfflineRules.OffLog();
        long t = 1_800_000_000_000L;
        log.off(t);
        log.off(t + 5000); // still off: keeps the first time
        log.on(t + 32 * 60_000L);
        assertEquals(1, log.closed.size());
        assertEquals(t, log.closed.get(0)[0]);
        String text = OfflineRules.OffLog.text("Bluetooth", log.closed.get(0)[0], log.closed.get(0)[1], TimeZone.getTimeZone("UTC"));
        assertTrue(text, text.startsWith("Bluetooth was off from "));
        assertTrue(text.contains(" to "));
    }
}
