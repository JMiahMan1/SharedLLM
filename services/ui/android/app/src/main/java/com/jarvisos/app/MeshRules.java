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
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * The offline chat's rules, free of Android so they run in plain JVM
 * tests ({@link FamilyMesh} does the radio).
 *
 * <h3>Messages</h3>
 * A message is a random 16-byte id, when it expires, how many phones it has
 * passed through, and a {@code sealed} envelope: the private chat's envelope
 * (AES-256-GCM, a fresh 12-byte IV, the 16-byte tag after the ciphertext;
 * {@link PrivateEnvelope}, services/common/sealed.py) under the key of the chat
 * room it was written in ({@link Room}: the server issues each member a key per
 * room, see services/gateway/family_mesh.py). Who wrote it, the room and the
 * words are inside the envelope; beside it in the clear is only a routing tag
 * ({@link #routeTag}: an HMAC of the room key, the period and the id), so a
 * phone that is in the room finds the key without trying them all, and a relay
 * or a listener sees only the id, sizes, the hop count and a tag that names no
 * room and differs for every message. Messages kept from before room keys carry
 * no tag and are sealed with the household's offline chat key.
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
    static final int STORE_MAX = 300;
    /** Cached household information is offered for up to a week (its age is shown). */
    static final long CACHE_TTL_S = 7 * 86400;

    /** Item types: a chat message; a Jarvis request and its answer (relayed);
     *  a cached household item (newest wins); a parent's command and the child's acknowledgement. */
    static final String T_CHAT = "chat";
    static final String T_REQUEST = "rq";
    static final String T_ANSWER = "rs";
    static final String T_CACHE = "cache";
    static final String T_COMMAND = "gc";
    static final String T_ACK = "ga";
    /**
     * One of a person's own items (OwnSyncRules): sealed with that person's key,
     * versioned like a cached item (newest wins), carried by every family phone
     * and opened only by the person's own devices.
     */
    static final String T_OWN = "own";
    static final int HOP_LIMIT = 4;
    static final int ID_BYTES = 16;

    static final byte F_HELLO = 'H';
    static final byte F_DIGEST = 'D';
    static final byte F_ITEM = 'I';
    static final byte F_END = 'E';
    /**
     * Who is connecting, between two devices of one person: the device id and
     * name sealed with that person's key (OwnSyncRules#who). An older phone
     * ignores it, and a family phone cannot open it.
     */
    static final byte F_WHO = 'W';
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

    // -- rooms ------------------------------------------------------------------------

    /** A routing tag's period: fixed here, so a phone never needs the proximity config to read one. */
    static final long ROOM_TAG_PERIOD_S = 6 * 3600;

    /** A chat room this phone's person is in, with the key the server issued for it. */
    static final class Room {
        final String room;
        final String name;
        final int type;
        final byte[] key;
        /** Keys the room had before its members last changed, which this person held. */
        final List<byte[]> previous;

        Room(String room, String name, int type, byte[] key, List<byte[]> previous) {
            this.room = room;
            this.name = name;
            this.type = type;
            this.key = key;
            this.previous = previous;
        }

        List<byte[]> keys() {
            List<byte[]> out = new ArrayList<>();
            out.add(key);
            out.addAll(previous);
            return out;
        }
    }

    /** A GET /api/family-mesh/rooms answer (or what was kept of one); bad entries are left out. */
    static List<Room> parseRooms(JSONObject answer) {
        List<Room> out = new ArrayList<>();
        JSONArray a = answer == null ? null : answer.optJSONArray("rooms");
        for (int k = 0; a != null && k < a.length(); k++) {
            JSONObject r = a.optJSONObject(k);
            if (r == null) continue;
            String token = r.optString("room", "");
            byte[] key = key32(r.optString("key", ""));
            if (!token.matches("[A-Za-z0-9]{4,64}") || key == null) continue;
            List<byte[]> previous = new ArrayList<>();
            JSONArray p = r.optJSONArray("previous");
            for (int j = 0; p != null && j < p.length(); j++) {
                byte[] old = key32(p.optString(j, ""));
                if (old != null) previous.add(old);
            }
            String name = r.optString("name", "").trim();
            out.add(new Room(token, name.isEmpty() ? token : name, r.optInt("type", 2), key, previous));
        }
        return out;
    }

    private static byte[] key32(String b64) {
        try {
            byte[] k = Base64.getDecoder().decode(b64);
            return k.length == 32 ? k : null;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    /** The rooms as the app shows them (never the keys). */
    static JSONArray roomsForApp(List<Room> rooms) {
        JSONArray out = new JSONArray();
        for (Room r : rooms) {
            try {
                out.put(new JSONObject().put("room", r.room).put("name", r.name).put("type", r.type));
            } catch (Exception ignored) {
                // Plain values: org.json does not throw here.
            }
        }
        return out;
    }

    static Room findRoom(List<Room> rooms, String room) {
        for (Room r : rooms) if (r.room.equals(room)) return r;
        return null;
    }

    static long tagPeriod(long sentS) {
        return Math.floorDiv(sentS, ROOM_TAG_PERIOD_S);
    }

    /**
     * The clear tag beside a room message: HMAC-SHA256 of the room key over the
     * period and the message id, 16 bytes in hex (services/gateway/family_mesh.py
     * route_tag). Only a holder of the key can make or recognise it, and it is
     * different for every message, so a relay cannot tell which room, nor that
     * two messages are from the same one.
     */
    static String routeTag(byte[] key, long period, String id) {
        return ProximityRules.hex(ProximityRules.hmac(key, "jarvis-mesh-room-v1|" + period + "|" + id), 16);
    }

    /** A new message of this phone's, sealed with its room's key and tagged. */
    static Item roomMessage(Room room, JSONObject payload, long nowS, SecureRandom random) throws Exception {
        String id = newId(random);
        Item i = new Item(id, nowS + TTL_S, 0, seal(room.key, payload, random));
        i.meta = new JSONObject().put("rt", routeTag(room.key, tagPeriod(nowS), id));
        i.mine = true;
        i.room = room.room;
        return i;
    }

    /**
     * A chat message opened, or null when this phone may not read it: a room
     * message opens only with the key of a room this person is in (the tag says
     * which, and the envelope must name that room); a message from before room
     * keys opens with the household chat key.
     */
    static JSONObject openChat(Item i, List<Room> rooms, byte[] householdKey) {
        if (i == null || !T_CHAT.equals(i.type)) return null;
        String tag = i.routeTag();
        if (tag == null) {
            // Before room keys (an older phone wrote it): the household chat key.
            return householdKey == null ? null : open(householdKey, i.env);
        }
        long period = tagPeriod(i.expiresS - TTL_S);
        for (Room r : rooms) {
            for (byte[] k : r.keys()) {
                if (!java.security.MessageDigest.isEqual(
                        routeTag(k, period, i.id).getBytes(StandardCharsets.US_ASCII),
                        tag.getBytes(StandardCharsets.US_ASCII))) {
                    continue;
                }
                JSONObject p = open(k, i.env);
                return p != null && r.room.equals(p.optString("room")) ? p : null;
            }
        }
        return null;
    }

    // -- the store ------------------------------------------------------------------

    /** One message as kept on a phone. {@code wire} is what travels; the rest is this phone's. */
    static final class Item {
        final String id;
        final long expiresS;
        final int hops;
        final JSONObject env;
        /** One of the T_* types (chat when absent, as older phones send). */
        String type = T_CHAT;
        /** For a cached item: the server's version (ms); newer replaces older. */
        long version;
        /** Clear fields a type carries beside its envelope (a request's signature, an item's addressee). */
        JSONObject meta;
        /** Written on this phone (it is this phone's job to post it). */
        boolean mine;
        /** Known to be in its chat. */
        boolean synced;
        long receivedAtMs;
        /** This phone's own message: the room it was written in (kept here, never sent to a peer). */
        String room;

        Item(String id, long expiresS, int hops, JSONObject env) {
            this.id = id;
            this.expiresS = expiresS;
            this.hops = hops;
            this.env = env;
        }

        JSONObject wire() throws Exception {
            JSONObject o = new JSONObject().put("id", id).put("exp", expiresS).put("hops", hops).put("env", env);
            if (!T_CHAT.equals(type)) o.put("type", type);
            if (version != 0) o.put("ver", version);
            if (meta != null) o.put("meta", meta);
            return o;
        }

        boolean isCache() {
            return T_CACHE.equals(type);
        }

        /** Newest version wins (cached household information, a person's own items); no hop limit. */
        boolean isVersioned() {
            return T_CACHE.equals(type) || T_OWN.equals(type);
        }

        JSONObject toJson() throws Exception {
            JSONObject o = wire().put("mine", mine).put("synced", synced).put("rx", receivedAtMs);
            if (mine && room != null) o.put("room", room);
            return o;
        }

        /** The clear routing tag beside a room message, or null (an older message, or another type). */
        String routeTag() {
            String t = meta == null ? "" : meta.optString("rt", "");
            return t.matches("[0-9a-f]{32}") ? t : null;
        }

        static Item fromWire(JSONObject o) {
            if (o == null) return null;
            String id = o.optString("id", "");
            JSONObject env = o.optJSONObject("env");
            if (!id.matches("[0-9a-f]{32}") || env == null || !"sealed".equals(env.optString("kind"))) return null;
            Item i = new Item(id, o.optLong("exp", 0L), Math.max(0, o.optInt("hops", 0)), env);
            i.type = o.optString("type", T_CHAT);
            if (!java.util.Arrays.asList(T_CHAT, T_REQUEST, T_ANSWER, T_CACHE, T_COMMAND, T_ACK, T_OWN).contains(i.type)) return null;
            i.version = o.optLong("ver", 0L);
            i.meta = o.optJSONObject("meta");
            i.mine = o.optBoolean("mine", false);
            i.synced = o.optBoolean("synced", false);
            i.receivedAtMs = o.optLong("rx", 0L);
            String room = o.optString("room", "");
            if (i.mine && room.matches("[A-Za-z0-9]{4,64}")) i.room = room;
            return i;
        }
    }

    /** A check a store makes before keeping an item (OwnSyncRules#acceptable on the person's own devices). */
    interface Acceptor {
        boolean accept(Item item);
    }

    static final class Store {
        final Map<String, Item> items = new LinkedHashMap<>();
        /** Null: keep anything well formed (a store that holds no own key). */
        Acceptor acceptor;

        /** Keep a message unless it is a duplicate, expired, too far travelled or junk; true when it is new. */
        boolean add(Item item, long nowS) {
            if (item == null) return false;
            long ttl = item.isVersioned() ? CACHE_TTL_S : TTL_S;
            if (item.expiresS <= nowS || item.expiresS > nowS + ttl + 3600) return false;
            if (!item.isVersioned() && item.hops > HOP_LIMIT) return false;
            Item had = items.get(item.id);
            if (had != null) {
                // Cached information and own items: the newest version wins, whoever brought it. An
                // equal version is the same copy, so nothing moves and nothing flaps.
                if (!item.isVersioned() || !item.type.equals(had.type) || item.version <= had.version) return false;
            }
            if (acceptor != null && !acceptor.accept(item)) return false;
            if (had != null) {
                items.remove(item.id);
            }
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

        /** What a peer can be offered: each id, and a cached item's version ({@code id@version}). */
        List<String> digest(long nowS) {
            List<String> out = new ArrayList<>();
            for (Item i : items.values()) if (i.expiresS > nowS) out.add(i.isVersioned() ? i.id + "@" + i.version : i.id);
            Collections.sort(out);
            return out;
        }

        /** What a peer holding {@code theirs} (id -> version) lacks or holds an older version of. */
        List<Item> missingFrom(Map<String, Long> theirs, long nowS) {
            List<Item> out = new ArrayList<>();
            for (Item i : items.values()) {
                if (i.expiresS <= nowS) continue;
                Long v = theirs.get(i.id);
                if (i.isVersioned()) {
                    if (v == null || v < i.version) out.add(i);
                } else if (i.hops < HOP_LIMIT && v == null) {
                    out.add(i);
                }
            }
            return out;
        }

        static Map<String, Long> parseDigest(List<String> tokens) {
            Map<String, Long> out = new HashMap<>();
            for (String t : tokens) {
                int at = t.indexOf('@');
                try {
                    out.put(at < 0 ? t : t.substring(0, at), at < 0 ? 0L : Long.parseLong(t.substring(at + 1)));
                } catch (NumberFormatException ignored) {
                    // A damaged token is just not offered.
                }
            }
            return out;
        }

        /** Received from a peer: one more hop than it had. */
        boolean receive(JSONObject wire, long nowS, long nowMs) {
            Item i = Item.fromWire(wire);
            if (i == null) return false;
            Item kept = new Item(i.id, i.expiresS, i.isVersioned() ? 0 : i.hops + 1, i.env);
            kept.type = i.type;
            kept.version = i.version;
            kept.meta = i.meta;
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

    /** The ids a /api/family-mesh/sync answer says are in their chat. */
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
        private Map<String, Long> theirs;
        boolean authorised;
        boolean done;
        int received;
        int sent;
        /** The ids this sync brought in (new, or a newer version), in order. */
        final List<String> receivedIds = new ArrayList<>();
        /** Between two devices of one person (setOwn): this device, and the other one once it has said who it is. */
        private byte[] ownKey;
        private String myDevice;
        private String myName;
        private boolean askWho;
        private SecureRandom random;
        String peerDevice;
        String peerName;

        Session(Store store, byte[] key, long period, int pieceSize, long nowMs) {
            this.store = store;
            this.key = key;
            this.period = period;
            this.pieceSize = pieceSize;
            this.nowMs = nowMs;
            this.nowS = nowMs / 1000L;
        }

        /**
         * This device can sync as one of its person's devices: the client
         * ({@code ask}) says who it is after HELLO and the server, when that
         * opens with the same person's key, answers with its own WHO.
         */
        void setOwn(byte[] key, String deviceId, String deviceName, boolean ask, SecureRandom r) {
            this.ownKey = key;
            this.myDevice = deviceId;
            this.myName = deviceName;
            this.askWho = ask;
            this.random = r;
        }

        private void take(byte[] body) throws Exception {
            JSONObject w = new JSONObject(new String(body, StandardCharsets.UTF_8));
            if (store.receive(w, nowS, nowMs)) {
                received++;
                receivedIds.add(w.optString("id"));
            }
        }

        private static byte[] digestBody(List<String> ids) {
            return new JSONArray(ids).toString().getBytes(StandardCharsets.UTF_8);
        }

        private static Map<String, Long> parseDigest(byte[] body) throws Exception {
            JSONArray a = new JSONArray(new String(body, StandardCharsets.UTF_8));
            List<String> tokens = new ArrayList<>();
            for (int i = 0; i < a.length(); i++) tokens.add(a.optString(i));
            return Store.parseDigest(tokens);
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
            if (ownKey != null && askWho) {
                try {
                    pieces.addAll(frames(F_WHO, OwnSyncRules.who(ownKey, myDevice, myName, period, random), pieceSize));
                } catch (Exception ignored) {
                    // Sealing plain JSON does not fail; without it the sync still runs, unnamed.
                }
            }
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
            } else if (type == F_WHO) {
                String[] w = OwnSyncRules.openWho(ownKey, body, period);
                if (w != null) {
                    peerDevice = w[0];
                    peerName = w[1];
                }
            } else if (type == F_ITEM) {
                take(body);
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
            if (type == F_WHO) {
                // Only another device of this phone's own person opens it; anyone else's is ignored.
                String[] w = OwnSyncRules.openWho(ownKey, body, period);
                if (w != null && peerDevice == null) {
                    peerDevice = w[0];
                    peerName = w[1];
                    queue(F_WHO, OwnSyncRules.who(ownKey, myDevice, myName, period, random));
                }
            } else if (type == F_DIGEST) {
                Map<String, Long> client = parseDigest(body);
                queue(F_DIGEST, digestBody(store.digest(nowS)));
                queueItems(store.missingFrom(client, nowS));
            } else if (type == F_ITEM) {
                take(body);
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
