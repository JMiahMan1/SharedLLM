package com.jarvisos.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collections;
import java.util.Deque;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * The offline family chat's rules, free of Android so they run in plain JVM
 * tests ({@link FamilyMesh} does the radio).
 *
 * <h3>Messages</h3>
 * A message is a random 16-byte id, when it expires, how many phones it has
 * passed through, and a {@code sealed} envelope: the private chat's envelope
 * (AES-256-GCM, a fresh 12-byte IV, the 16-byte tag after the ciphertext;
 * {@link PrivateEnvelope}, services/common/sealed.py) under the household's
 * offline chat key. Who wrote it, the conversation and the words are inside the
 * envelope; a relay or a listener sees only the id, sizes and the hop count.
 * Kinds: {@code text} (at most {@link #TEXT_MAX} characters), {@code pin} (a
 * place) and {@code thumb} (a JPEG preview of at most {@link #THUMB_MAX} bytes).
 *
 * <h3>Store and forward</h3>
 * Every phone keeps the family's offline messages for {@link #TTL_S} (at most
 * {@link #STORE_MAX}) and passes on what a peer lacks, up to {@link #HOP_LIMIT}
 * phones from the author. The id is the only key: a message arriving twice is
 * kept once.
 *
 * <h3>A sync between two phones</h3>
 * Over one short GATT connection, transport-agnostic here ({@link Session}):
 * the phone that connects writes HELLO (a proof it holds the household key for
 * this period) and its DIGEST (the ids it has); the other answers with its
 * DIGEST and the messages the first lacks, then END; the first then writes the
 * messages the second lacks and END, and disconnects. Frames are cut to the
 * connection's size and reassembled ({@link #frames}, {@link Reassembler}).
 */
final class MeshRules {
    static final int TEXT_MAX = 2000;
    static final int THUMB_MAX = 24 * 1024;
    static final long TTL_S = 72 * 3600;
    static final int STORE_MAX = 200;
    static final int HOP_LIMIT = 4;
    static final int ID_BYTES = 16;

    static final byte F_HELLO = 'H';
    static final byte F_DIGEST = 'D';
    static final byte F_ITEM = 'I';
    static final byte F_END = 'E';
    /** Frame header: type, and whether more pieces of the same frame follow. */
    static final int HEADER = 2;

    private MeshRules() {}

    // -- the envelope ---------------------------------------------------------------

    static JSONObject seal(byte[] key, JSONObject payload, SecureRandom random) throws Exception {
        byte[] iv = new byte[12];
        random.nextBytes(iv);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, iv));
        byte[] ct = c.doFinal(payload.toString().getBytes(StandardCharsets.UTF_8));
        return new JSONObject().put("kind", "sealed").put("v", 1)
                .put("iv", Base64.getEncoder().encodeToString(iv))
                .put("ct", Base64.getEncoder().encodeToString(ct));
    }

    /** The payload, or null when the envelope does not open with {@code key}. */
    static JSONObject open(byte[] key, JSONObject env) {
        if (env == null || key == null || !"sealed".equals(env.optString("kind"))) return null;
        byte[] plain = null;
        try {
            byte[] iv = Base64.getDecoder().decode(env.getString("iv"));
            byte[] ct = Base64.getDecoder().decode(env.getString("ct"));
            if (iv.length != 12) return null;
            Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, iv));
            plain = c.doFinal(ct);
            return new JSONObject(new String(plain, StandardCharsets.UTF_8));
        } catch (Exception e) {
            return null;
        } finally {
            if (plain != null) Arrays.fill(plain, (byte) 0);
        }
    }

    /** A new message's payload, checked; IllegalArgumentException says what is wrong. */
    static JSONObject payload(String kind, String from, String room, JSONObject fields, long nowS) throws Exception {
        JSONObject p = new JSONObject().put("t", kind).put("from", from).put("room", room).put("sent_at", nowS);
        switch (kind) {
            case "text": {
                String text = fields.optString("text", "").trim();
                if (text.isEmpty()) throw new IllegalArgumentException("Type a message first");
                if (text.length() > TEXT_MAX) throw new IllegalArgumentException("That message is too long to send offline");
                return p.put("text", text);
            }
            case "pin": {
                double lat = fields.optDouble("lat", Double.NaN);
                double lon = fields.optDouble("lon", Double.NaN);
                if (!(lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180)) {
                    throw new IllegalArgumentException("That is not a place");
                }
                String label = fields.optString("label", "").trim();
                return p.put("lat", lat).put("lon", lon).put("label", label.length() > 120 ? label.substring(0, 120) : label);
            }
            case "thumb": {
                String data = fields.optString("data", "");
                byte[] bytes;
                try {
                    bytes = Base64.getDecoder().decode(data);
                } catch (IllegalArgumentException e) {
                    throw new IllegalArgumentException("That picture could not be read");
                }
                if (bytes.length == 0 || bytes.length > THUMB_MAX) {
                    throw new IllegalArgumentException("Offline, only a small preview of a photo can be sent");
                }
                String caption = fields.optString("caption", "").trim();
                return p.put("mime", "image/jpeg").put("data", data)
                        .put("caption", caption.length() > 300 ? caption.substring(0, 300) : caption);
            }
            default:
                throw new IllegalArgumentException("Unknown kind of message");
        }
    }

    static String newId(SecureRandom random) {
        byte[] b = new byte[ID_BYTES];
        random.nextBytes(b);
        return ProximityRules.hex(b, ID_BYTES);
    }

    // -- the store ------------------------------------------------------------------

    /** One message as kept on a phone. {@code wire} is what travels; the rest is this phone's. */
    static final class Item {
        final String id;
        final long expiresS;
        final int hops;
        final JSONObject env;
        /** Written on this phone (it is this phone's job to post it). */
        boolean mine;
        /** Known to be in the family chat. */
        boolean synced;
        long receivedAtMs;

        Item(String id, long expiresS, int hops, JSONObject env) {
            this.id = id;
            this.expiresS = expiresS;
            this.hops = hops;
            this.env = env;
        }

        JSONObject wire() throws Exception {
            return new JSONObject().put("id", id).put("exp", expiresS).put("hops", hops).put("env", env);
        }

        JSONObject toJson() throws Exception {
            return wire().put("mine", mine).put("synced", synced).put("rx", receivedAtMs);
        }

        static Item fromWire(JSONObject o) {
            if (o == null) return null;
            String id = o.optString("id", "");
            JSONObject env = o.optJSONObject("env");
            if (!id.matches("[0-9a-f]{32}") || env == null || !"sealed".equals(env.optString("kind"))) return null;
            Item i = new Item(id, o.optLong("exp", 0L), Math.max(0, o.optInt("hops", 0)), env);
            i.mine = o.optBoolean("mine", false);
            i.synced = o.optBoolean("synced", false);
            i.receivedAtMs = o.optLong("rx", 0L);
            return i;
        }
    }

    static final class Store {
        final Map<String, Item> items = new LinkedHashMap<>();

        /** Keep a message unless it is a duplicate, expired, too far travelled or junk; true when it is new. */
        boolean add(Item item, long nowS) {
            if (item == null || items.containsKey(item.id)) return false;
            if (item.expiresS <= nowS || item.expiresS > nowS + TTL_S + 3600 || item.hops > HOP_LIMIT) return false;
            items.put(item.id, item);
            trim(nowS);
            return true;
        }

        /** Drop what expired, then the oldest past the cap (the phone's own unsynced ones last). */
        void trim(long nowS) {
            items.values().removeIf(i -> i.expiresS <= nowS && (!i.mine || i.synced));
            if (items.size() <= STORE_MAX) return;
            List<Item> order = new ArrayList<>(items.values());
            order.sort((a, b) -> {
                boolean ka = a.mine && !a.synced;
                boolean kb = b.mine && !b.synced;
                if (ka != kb) return ka ? 1 : -1;
                return Long.compare(a.expiresS, b.expiresS);
            });
            for (int k = 0; items.size() > STORE_MAX && k < order.size(); k++) items.remove(order.get(k).id);
        }

        /** The ids a peer can be offered (messages that may still travel). */
        List<String> digest(long nowS) {
            List<String> out = new ArrayList<>();
            for (Item i : items.values()) if (i.expiresS > nowS) out.add(i.id);
            Collections.sort(out);
            return out;
        }

        /** What a peer holding {@code theirs} lacks and may still be given. */
        List<Item> missingFrom(Set<String> theirs, long nowS) {
            List<Item> out = new ArrayList<>();
            for (Item i : items.values()) {
                if (i.expiresS > nowS && i.hops < HOP_LIMIT && !theirs.contains(i.id)) out.add(i);
            }
            return out;
        }

        /** Received from a peer: one more hop than it had. */
        boolean receive(JSONObject wire, long nowS, long nowMs) {
            Item i = Item.fromWire(wire);
            if (i == null) return false;
            Item kept = new Item(i.id, i.expiresS, i.hops + 1, i.env);
            kept.receivedAtMs = nowMs;
            return add(kept, nowS);
        }

        List<Item> unsyncedMine() {
            List<Item> out = new ArrayList<>();
            for (Item i : items.values()) if (i.mine && !i.synced) out.add(i);
            return out;
        }

        void markSynced(Iterable<String> ids) {
            for (String id : ids) {
                Item i = items.get(id);
                if (i != null) i.synced = true;
            }
        }

        String encode() throws Exception {
            JSONArray a = new JSONArray();
            for (Item i : items.values()) a.put(i.toJson());
            return a.toString();
        }

        static Store decode(String text) {
            Store s = new Store();
            try {
                JSONArray a = new JSONArray(text == null || text.isEmpty() ? "[]" : text);
                for (int k = 0; k < a.length(); k++) {
                    Item i = Item.fromWire(a.optJSONObject(k));
                    if (i != null) s.items.put(i.id, i);
                }
            } catch (Exception ignored) {
                // A damaged store starts empty rather than stopping the chat.
            }
            return s;
        }
    }

    /** Whether a phone holds messages that may still travel (the advertisement's flag). */
    static boolean hasMail(Store s, long nowS) {
        for (Item i : s.items.values()) if (i.expiresS > nowS && i.hops < HOP_LIMIT) return true;
        return false;
    }

    /** A short summary of what a store can offer, to tell whether it changed since a sync. */
    static long digestHash(Store s, long nowS) {
        long h = 1125899906842597L;
        for (String id : s.digest(nowS)) h = 31 * h + id.hashCode();
        return h;
    }

    /** Minimum time between two syncs with the same phone when nothing changed on this one. */
    static final long RESYNC_MS = 10 * 60_000L;
    /** A sync just happened (or is under way): leave it. */
    static final long MIN_GAP_MS = 60_000L;
    /** If the phone that should connect has not, after this long the other one does. */
    static final long FALLBACK_MS = 2 * 60_000L;

    /**
     * Whether to connect to a family phone just heard: only when there is
     * something to pass on (this phone changed since the last sync with it, or
     * the other phone's advertised tag changed, or it has messages and it has
     * been a while), only to a phone that accepts
     * connections, and, when both could, by the phone whose name sorts first so
     * the two do not connect to each other at once (the other one steps in after
     * a while, or always when it cannot accept connections itself).
     */
    static boolean shouldConnect(boolean peerCanConnect, boolean peerHasMail, boolean iHaveMail, long sinceLastMs,
                                 boolean changedSinceLast, boolean peerChangedSinceLast, boolean iSortFirst,
                                 boolean iCanServe) {
        if (!peerCanConnect || sinceLastMs < MIN_GAP_MS) return false;
        boolean want = (iHaveMail && changedSinceLast)
                || (peerHasMail && (peerChangedSinceLast || sinceLastMs >= RESYNC_MS));
        if (!want) return false;
        return iSortFirst || !iCanServe || sinceLastMs >= FALLBACK_MS;
    }

    /** The ids a /api/family-mesh/sync answer says are in the family chat. */
    static List<String> postedIds(JSONObject answer) {
        List<String> out = new ArrayList<>();
        JSONObject results = answer == null ? null : answer.optJSONObject("results");
        if (results == null) return out;
        for (java.util.Iterator<String> it = results.keys(); it.hasNext(); ) {
            String id = it.next();
            if ("posted".equals(results.optString(id))) out.add(id);
        }
        Collections.sort(out);
        return out;
    }

    // -- frames -----------------------------------------------------------------------

    /** One logical frame cut into pieces of at most {@code size} bytes (header included). */
    static List<byte[]> frames(byte type, byte[] body, int size) {
        int room = Math.max(1, size - HEADER);
        List<byte[]> out = new ArrayList<>();
        int off = 0;
        do {
            int n = Math.min(room, body.length - off);
            byte[] piece = new byte[HEADER + n];
            piece[0] = type;
            piece[1] = (byte) (off + n < body.length ? 1 : 0);
            System.arraycopy(body, off, piece, HEADER, n);
            out.add(piece);
            off += n;
        } while (off < body.length);
        return out;
    }

    /** Pieces back into whole frames: {type, body}, or null while one is incomplete. */
    static final class Reassembler {
        private final ByteArrayOutputStream buf = new ByteArrayOutputStream();
        private byte type;
        private boolean open;
        /** A frame larger than this is refused (a thumbnail, sealed and base64'd, with room to spare). */
        static final int MAX_FRAME = 64 * 1024;

        Object[] add(byte[] piece) {
            if (piece == null || piece.length < HEADER) throw new IllegalStateException("short piece");
            if (open && piece[0] != type) throw new IllegalStateException("interleaved frames");
            type = piece[0];
            open = true;
            buf.write(piece, HEADER, piece.length - HEADER);
            if (buf.size() > MAX_FRAME) throw new IllegalStateException("frame too large");
            if (piece[1] != 0) return null;
            byte[] body = buf.toByteArray();
            buf.reset();
            open = false;
            return new Object[] {type, body};
        }
    }

    /** The HELLO proof: only a phone holding this period's household key can make it. */
    static byte[] hello(byte[] key, long period) {
        return Arrays.copyOf(ProximityRules.hmac(key, "jarvis-mesh-hello-v1|" + period), 16);
    }

    static boolean helloOk(byte[] key, long period, byte[] proof) {
        for (long p = period - 1; p <= period + 1; p++) {
            if (java.security.MessageDigest.isEqual(hello(key, p), proof)) return true;
        }
        return false;
    }

    // -- a sync session, either side -------------------------------------------------

    /**
     * One sync over one connection. The connecting side (client) calls
     * {@link #clientOpening}, sends those pieces, then {@link #clientRead}s the
     * answer piece by piece until it returns its own closing pieces. The other
     * side (server) feeds each written piece to {@link #serverWrite} and serves
     * {@link #serverNextRead} until it is empty.
     */
    static final class Session {
        final Store store;
        final byte[] key;
        final long period;
        final int pieceSize;
        final long nowS;
        final long nowMs;
        private final Reassembler in = new Reassembler();
        private final Deque<byte[]> out = new ArrayDeque<>();
        private Set<String> theirs;
        boolean authorised;
        boolean done;
        int received;
        int sent;

        Session(Store store, byte[] key, long period, int pieceSize, long nowMs) {
            this.store = store;
            this.key = key;
            this.period = period;
            this.pieceSize = pieceSize;
            this.nowMs = nowMs;
            this.nowS = nowMs / 1000L;
        }

        private static byte[] digestBody(List<String> ids) {
            return new JSONArray(ids).toString().getBytes(StandardCharsets.UTF_8);
        }

        private static Set<String> parseDigest(byte[] body) throws Exception {
            JSONArray a = new JSONArray(new String(body, StandardCharsets.UTF_8));
            Set<String> s = new HashSet<>();
            for (int i = 0; i < a.length(); i++) s.add(a.optString(i));
            return s;
        }

        private void queue(byte type, byte[] body) {
            out.addAll(frames(type, body, pieceSize));
        }

        private void queueItems(List<Item> items) throws Exception {
            for (Item i : items) {
                queue(F_ITEM, i.wire().toString().getBytes(StandardCharsets.UTF_8));
                sent++;
            }
            queue(F_END, new byte[0]);
        }

        /** Client: HELLO and DIGEST, to write. */
        List<byte[]> clientOpening() {
            List<byte[]> pieces = new ArrayList<>(frames(F_HELLO, hello(key, period), pieceSize));
            pieces.addAll(frames(F_DIGEST, digestBody(store.digest(nowS)), pieceSize));
            return pieces;
        }

        /**
         * Client: one piece read from the server. Returns the pieces to write
         * back once the server's END arrives (the messages it lacks, then END),
         * else an empty list.
         */
        List<byte[]> clientRead(byte[] piece) throws Exception {
            Object[] f = in.add(piece);
            if (f == null) return Collections.emptyList();
            byte type = (byte) f[0];
            byte[] body = (byte[]) f[1];
            if (type == F_DIGEST) {
                theirs = parseDigest(body);
            } else if (type == F_ITEM) {
                if (store.receive(new JSONObject(new String(body, StandardCharsets.UTF_8)), nowS, nowMs)) received++;
            } else if (type == F_END) {
                if (theirs == null) throw new IllegalStateException("no digest");
                // What the server lacks, now including nothing it just sent us.
                List<Item> give = store.missingFrom(theirs, nowS);
                out.clear();
                queueItems(give);
                List<byte[]> pieces = new ArrayList<>(out);
                out.clear();
                done = true;
                return pieces;
            }
            return Collections.emptyList();
        }

        /** Server: one piece the client wrote. False means refuse and disconnect. */
        boolean serverWrite(byte[] piece) throws Exception {
            Object[] f = in.add(piece);
            if (f == null) return true;
            byte type = (byte) f[0];
            byte[] body = (byte[]) f[1];
            if (type == F_HELLO) {
                authorised = helloOk(key, period, body);
                return authorised;
            }
            if (!authorised) return false;
            if (type == F_DIGEST) {
                Set<String> client = parseDigest(body);
                queue(F_DIGEST, digestBody(store.digest(nowS)));
                queueItems(store.missingFrom(client, nowS));
            } else if (type == F_ITEM) {
                if (store.receive(new JSONObject(new String(body, StandardCharsets.UTF_8)), nowS, nowMs)) received++;
            } else if (type == F_END) {
                done = true;
            }
            return true;
        }

        /** Server: the next piece for the client to read, or null when nothing is queued. */
        byte[] serverNextRead() {
            return out.poll();
        }
    }
}
