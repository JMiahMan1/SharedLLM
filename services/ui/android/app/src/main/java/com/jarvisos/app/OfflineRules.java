package com.jarvisos.app;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Offline mode's rules, free of Android so they run in plain JVM tests: Jarvis
 * requests relayed by another family phone, the cached household information,
 * a parent's commands to a child's phone, and the Bluetooth-off log.
 * services/gateway/offline_relay.py, offline_cache.py and offline_guard.py are
 * the server's halves and build the same bytes.
 *
 * <h3>Relayed requests</h3>
 * Signed (HMAC-SHA256) and sealed (the private chat's AES-GCM envelope) with
 * two keys derived from the phone's own Jarvis API key, which the server can
 * derive too and a relay cannot: a relay can carry a request but not read,
 * change or make one in someone else's name.
 *
 * <h3>Parent commands</h3>
 * A child's phone has to check a command itself, offline, so these are signed
 * with an EC P-256 key pair (SHA256withECDSA; the private half in the Android
 * Keystore): the child holds only its parents' public keys and cannot sign.
 * It accepts a command only when it is for this child, from a parent device it
 * holds a key for, correctly signed, not expired, not seen before, and with a
 * sequence number above the last one it accepted from that device, so an old
 * unlock played again is refused.
 */
final class OfflineRules {
    static final String REQUEST_VERSION = "jarvis-offline-request-v1";
    static final String COMMAND_VERSION = "jarvis-guard-command-v1";
    static final String ACK_VERSION = "jarvis-guard-ack-v1";

    /** Longest each relayed kind may live (seconds), as the gateway allows. */
    static long lifetimeS(String kind) {
        switch (kind) {
            case "command": return 10 * 60;
            case "location": return 30 * 60;
            case "ask": return 60 * 60;
            case "shopping_add":
            case "shopping_check": return 24 * 3600;
            default: return 0;
        }
    }

    private OfflineRules() {}

    // -- relayed requests -------------------------------------------------------------

    /** {sign_key, seal_key} from a Jarvis API key. */
    static byte[][] deriveKeys(String apiKey) {
        byte[] k = apiKey.getBytes(StandardCharsets.UTF_8);
        return new byte[][] {ProximityRules.hmac(k, "jarvis-offline-sign-v1"), ProximityRules.hmac(k, "jarvis-offline-seal-v1")};
    }

    static byte[] requestBytes(JSONObject r) {
        JSONObject env = r.optJSONObject("env");
        String[] parts = {REQUEST_VERSION, r.optString("id"), r.optString("from"), r.optString("kind"),
            Long.toString(r.optLong("issued_at")), Long.toString(r.optLong("expires_at")),
            env == null ? "" : env.optString("iv"), env == null ? "" : env.optString("ct")};
        return String.join("|", parts).getBytes(StandardCharsets.UTF_8);
    }

    static String hmacB64(byte[] key, byte[] data) {
        try {
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(key, "HmacSHA256"));
            return Base64.getEncoder().encodeToString(mac.doFinal(data));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    /** A signed, sealed request, ready to travel. */
    static JSONObject request(String apiKey, String id, String from, String kind, JSONObject body, long nowS,
                              SecureRandom random) throws Exception {
        long life = lifetimeS(kind);
        if (life == 0) throw new IllegalArgumentException("Unknown request");
        byte[][] keys = deriveKeys(apiKey);
        JSONObject r = new JSONObject().put("id", id).put("from", from).put("kind", kind)
                .put("issued_at", nowS).put("expires_at", nowS + life)
                .put("env", MeshRules.seal(keys[1], body, random));
        return r.put("sig", hmacB64(keys[0], requestBytes(r)));
    }

    /** Whether a request is signed with {@code apiKey}'s key (the gateway checks the same way). */
    static boolean requestSignedBy(String apiKey, JSONObject r) {
        byte[] expected = Base64.getDecoder().decode(hmacB64(deriveKeys(apiKey)[0], requestBytes(r)));
        byte[] given;
        try {
            given = Base64.getDecoder().decode(r.optString("sig", ""));
        } catch (IllegalArgumentException e) {
            return false;
        }
        return MessageDigest.isEqual(expected, given);
    }

    /** What a queued request says to its sender now. Never silent. */
    static String requestStatus(boolean answered, boolean expired, boolean anyPeerRecently, boolean online) {
        if (answered) return "answered";
        if (expired) return "expired";
        if (online) return "sending";
        return anyPeerRecently ? "waiting_for_online_phone" : "no_family_phone_nearby";
    }

    // -- the cache -------------------------------------------------------------------------

    /** The mesh id of a cached item: one per (member, key), so a newer version replaces it. */
    static String cacheId(String to, String key) {
        try {
            byte[] h = MessageDigest.getInstance("SHA-256").digest(("cache|" + to + "|" + key).getBytes(StandardCharsets.UTF_8));
            return ProximityRules.hex(h, MeshRules.ID_BYTES);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    // -- parent commands --------------------------------------------------------------------

    static byte[] commandBytes(JSONObject c) {
        String[] parts = {COMMAND_VERSION, c.optString("id"), c.optString("parent"), c.optString("device"),
            c.optString("child"), c.optString("action"), Long.toString(c.optLong("arg")),
            Long.toString(c.optLong("issued_at")), Long.toString(c.optLong("expires_at")), Long.toString(c.optLong("seq"))};
        return String.join("|", parts).getBytes(StandardCharsets.UTF_8);
    }

    static byte[] ackBytes(JSONObject a) {
        String[] parts = {ACK_VERSION, a.optString("id"), a.optString("child"), a.optString("device"),
            Long.toString(a.optLong("applied_at")), a.optString("result")};
        return String.join("|", parts).getBytes(StandardCharsets.UTF_8);
    }

    static String ecSign(PrivateKey key, byte[] data) throws Exception {
        Signature s = Signature.getInstance("SHA256withECDSA");
        s.initSign(key);
        s.update(data);
        return Base64.getEncoder().encodeToString(s.sign());
    }

    static PublicKey publicKey(String x509b64) throws Exception {
        return KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(Base64.getDecoder().decode(x509b64)));
    }

    static boolean ecVerify(String x509b64, byte[] data, String sigB64) {
        try {
            Signature s = Signature.getInstance("SHA256withECDSA");
            s.initVerify(publicKey(x509b64));
            s.update(data);
            return s.verify(Base64.getDecoder().decode(sigB64));
        } catch (Exception e) {
            return false;
        }
    }

    /** A parent's command, signed by the parent's device. {@code arg}: minutes (lock length, bonus). */
    static JSONObject command(PrivateKey key, String id, String parent, String device, String child, String action,
                              int arg, long nowS, long lifeS, long seq) throws Exception {
        JSONObject c = new JSONObject().put("id", id).put("parent", parent).put("device", device).put("child", child)
                .put("action", action).put("arg", arg).put("issued_at", nowS).put("expires_at", nowS + lifeS).put("seq", seq);
        return c.put("sig", ecSign(key, commandBytes(c)));
    }

    static JSONObject ack(PrivateKey key, String commandId, String child, String device, long appliedAtS, String result)
            throws Exception {
        JSONObject a = new JSONObject().put("id", commandId).put("child", child).put("device", device)
                .put("applied_at", appliedAtS).put("result", result);
        return a.put("sig", ecSign(key, ackBytes(a)));
    }

    /** Why a child's phone refused a command, or OK. */
    enum Verdict { OK, NOT_FOR_ME, UNKNOWN_SIGNER, BAD_SIGNATURE, EXPIRED, FROM_THE_FUTURE, ALREADY_SEEN, OUT_OF_ORDER, MALFORMED }

    /**
     * What a child's phone keeps to check commands offline: its parents' device
     * keys ({@code parent/device -> public key}, from the server, versioned), the
     * highest sequence accepted per parent device, and the ids it has seen.
     */
    static final class ChildGuard {
        final Map<String, String> parentKeys = new HashMap<>();
        final Map<String, Long> lastSeq = new HashMap<>();
        final Set<String> seen = new LinkedHashSet<>();
        String keysVersion = "";

        Verdict check(JSONObject c, String me, long nowS) {
            if (c == null || !c.optString("id").matches("[0-9a-f]{32}") || c.optLong("seq") < 1) return Verdict.MALFORMED;
            if (!me.equals(c.optString("child"))) return Verdict.NOT_FOR_ME;
            String signer = c.optString("parent") + "/" + c.optString("device");
            String pub = parentKeys.get(signer);
            if (pub == null) return Verdict.UNKNOWN_SIGNER;
            if (!ecVerify(pub, commandBytes(c), c.optString("sig"))) return Verdict.BAD_SIGNATURE;
            if (c.optLong("issued_at") > nowS + 300) return Verdict.FROM_THE_FUTURE;
            if (c.optLong("expires_at") <= nowS) return Verdict.EXPIRED;
            if (seen.contains(c.optString("id"))) return Verdict.ALREADY_SEEN;
            Long last = lastSeq.get(signer);
            if (last != null && c.optLong("seq") <= last) return Verdict.OUT_OF_ORDER;
            return Verdict.OK;
        }

        /** Remember an accepted command, so it and anything older from that device are refused from now on. */
        void accept(JSONObject c) {
            seen.add(c.optString("id"));
            while (seen.size() > 500) seen.remove(seen.iterator().next());
            lastSeq.put(c.optString("parent") + "/" + c.optString("device"), c.optLong("seq"));
        }

        JSONObject toJson() throws Exception {
            return new JSONObject().put("keys", new JSONObject(parentKeys)).put("seq", new JSONObject(lastSeq))
                    .put("seen", new org.json.JSONArray(seen)).put("version", keysVersion);
        }

        static ChildGuard fromJson(JSONObject o) {
            ChildGuard g = new ChildGuard();
            if (o == null) return g;
            JSONObject keys = o.optJSONObject("keys");
            for (java.util.Iterator<String> it = keys == null ? null : keys.keys(); it != null && it.hasNext(); ) {
                String k = it.next();
                g.parentKeys.put(k, keys.optString(k));
            }
            JSONObject seq = o.optJSONObject("seq");
            for (java.util.Iterator<String> it = seq == null ? null : seq.keys(); it != null && it.hasNext(); ) {
                String k = it.next();
                g.lastSeq.put(k, seq.optLong(k));
            }
            org.json.JSONArray seen = o.optJSONArray("seen");
            for (int i = 0; seen != null && i < seen.length(); i++) g.seen.add(seen.optString(i));
            g.keysVersion = o.optString("version", "");
            return g;
        }

        /** Take a GET /api/offline-keys answer's parent keys (sequences and seen ids are kept). */
        void setKeys(JSONObject answer) {
            parentKeys.clear();
            org.json.JSONArray parents = answer == null ? null : answer.optJSONArray("parents");
            for (int i = 0; parents != null && i < parents.length(); i++) {
                JSONObject p = parents.optJSONObject(i);
                if (p != null) parentKeys.put(p.optString("user") + "/" + p.optString("device"), p.optString("pub"));
            }
            keysVersion = answer == null ? "" : answer.optString("version", "");
        }
    }

    /**
     * What the phone should do for an accepted command: the lock it now holds
     * (until when, 0 = until a parent lifts it, -1 = none) and the extra minutes.
     * The most recent decision wins: commands are applied in signed-time then
     * sequence order, so a later unlock lifts an earlier lock and the reverse.
     */
    static final class Overlay {
        long lockUntilMs = -1;
        long lockSetAtS;
        long unlockedUntilMs;

        void apply(JSONObject c, long nowMs) {
            long at = c.optLong("issued_at");
            if (at < lockSetAtS) return; // an older decision than the one in force
            switch (c.optString("action")) {
                case "lock":
                    long minutes = c.optLong("arg");
                    lockUntilMs = minutes > 0 ? at * 1000L + minutes * 60_000L : 0;
                    unlockedUntilMs = 0;
                    lockSetAtS = at;
                    break;
                case "unlock":
                    lockUntilMs = -1;
                    lockSetAtS = at;
                    break;
                case "bonus":
                    unlockedUntilMs = Math.max(unlockedUntilMs, nowMs) + c.optLong("arg") * 60_000L;
                    break;
                default:
                    break;
            }
        }

        boolean locked(long nowMs) {
            return lockUntilMs == 0 || lockUntilMs > nowMs;
        }

        JSONObject toJson() throws Exception {
            return new JSONObject().put("lock_until", lockUntilMs).put("lock_at", lockSetAtS).put("unlocked_until", unlockedUntilMs);
        }

        static Overlay fromJson(JSONObject o) {
            Overlay v = new Overlay();
            if (o == null) return v;
            v.lockUntilMs = o.optLong("lock_until", -1);
            v.lockSetAtS = o.optLong("lock_at", 0);
            v.unlockedUntilMs = o.optLong("unlocked_until", 0);
            return v;
        }
    }

    // -- Bluetooth off -----------------------------------------------------------------------

    /** Times Bluetooth (or airplane mode) was off, closed when it came back. */
    static final class OffLog {
        long offSinceMs;
        final List<long[]> closed = new ArrayList<>();

        void off(long nowMs) {
            if (offSinceMs == 0) offSinceMs = nowMs;
        }

        void on(long nowMs) {
            if (offSinceMs > 0 && nowMs > offSinceMs) closed.add(new long[] {offSinceMs, nowMs});
            offSinceMs = 0;
        }

        /** The report for parents, e.g. "Bluetooth was off from 3:10 PM to 3:42 PM". */
        static String text(String what, long fromMs, long toMs, java.util.TimeZone zone) {
            java.text.SimpleDateFormat t = new java.text.SimpleDateFormat("h:mm a", java.util.Locale.US);
            java.text.SimpleDateFormat d = new java.text.SimpleDateFormat("MMM d, h:mm a", java.util.Locale.US);
            t.setTimeZone(zone);
            d.setTimeZone(zone);
            java.text.SimpleDateFormat day = new java.text.SimpleDateFormat("yyyyMMdd", java.util.Locale.US);
            day.setTimeZone(zone);
            boolean sameDay = day.format(fromMs).equals(day.format(toMs));
            return what + " was off " + span(fromMs, toMs, zone);
        }

        /** "from 3:10 PM to 3:42 PM" (with the dates when it crossed midnight), for the parents' report. */
        static String span(long fromMs, long toMs, java.util.TimeZone zone) {
            java.text.SimpleDateFormat t = new java.text.SimpleDateFormat("h:mm a", java.util.Locale.US);
            java.text.SimpleDateFormat d = new java.text.SimpleDateFormat("MMM d, h:mm a", java.util.Locale.US);
            java.text.SimpleDateFormat day = new java.text.SimpleDateFormat("yyyyMMdd", java.util.Locale.US);
            t.setTimeZone(zone);
            d.setTimeZone(zone);
            day.setTimeZone(zone);
            java.text.SimpleDateFormat f = day.format(fromMs).equals(day.format(toMs)) ? t : d;
            return "from " + f.format(fromMs) + " to " + f.format(toMs);
        }
    }
}
