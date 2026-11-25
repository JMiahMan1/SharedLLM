package com.jarvisos.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * A person's own devices (phone, tablet, and the Jarvis Watch through either)
 * keeping that person's data in step over Bluetooth LE with no network, free of
 * Android so the rules run in plain JVM tests ({@link OwnSync} does the rest).
 *
 * <h3>What travels</h3>
 * Own items ride in {@link FamilyMesh}'s store as {@link MeshRules#T_OWN}, next to
 * the chat, the relayed requests, the household snapshot and the parent commands
 * (which already travel between every family phone, the person's own included).
 * An own item is one of:
 * <ul>
 *   <li>{@code device:<device id>}: one device's name, battery and its own last
 *       location fix (written only by that device);</li>
 *   <li>{@code steps:<device id>}: that device's own step count per day for the
 *       last {@link #STEP_DAYS} days, each with its updated-at and its hours
 *       (written only by that device);</li>
 *   <li>{@code watch}: the Jarvis Watch's last stats (steps, battery), written by
 *       whichever of the person's phones read the watch last;</li>
 *   <li>{@code rooms}: the person's offline chat rooms and their keys, as last
 *       fetched by any of their devices.</li>
 * </ul>
 *
 * <h3>Newest wins, never flapping</h3>
 * Each item's mesh version is its updated-at (ms) shifted left by
 * {@link #RANK_BITS}, with the writer's rank (a keyed hash of its device id) in
 * the low bits. The store keeps a version only over a strictly lower one
 * ({@link MeshRules.Store#add}), and digests carry {@code id@version}, so a sync
 * sends the newer copy to the device holding the older one and nothing when they
 * are equal. Two writers at the same millisecond differ in rank, so the same
 * copy wins on every device; an equal version is the same copy and is never sent
 * back and forth.
 *
 * <h3>Private to the person</h3>
 * Sealed with the person's offline seal key from PR #216
 * ({@link OfflineRules#deriveKeys}: HMAC-SHA256 of their Jarvis API key, which
 * every device of theirs holds and only the server can derive besides), in the
 * same AES-256-GCM envelope. A family phone relaying it sees an id, a version and
 * a size. The id is keyed too ({@link #itemId}: its second half is a MAC of its
 * first), so the person's devices recognise their own ids and refuse a copy whose
 * clear version was altered on the way ({@link #acceptable}).
 */
final class OwnSyncRules {
    static final String K_DEVICE = "device";
    static final String K_STEPS = "steps";
    static final String K_WATCH = "watch";
    static final String K_ROOMS = "rooms";
    /** Days of step counts each device carries. */
    static final int STEP_DAYS = 7;
    static final int RANK_BITS = 16;
    /** The pseudo device the watch's steps count as. */
    static final String WATCH_DEVICE = "watch";

    /** Diagnostics: what each sync can bring, in the order shown. */
    static final String C_DEVICE = "device";
    static final String C_STEPS = "steps";
    static final String C_WATCH = "watch";
    static final String C_ROOMS = "rooms";
    static final String C_FAMILY = "family";
    static final String C_CHAT = "chat";
    static final String C_REQUESTS = "requests";
    static final String C_GUARDIAN = "guardian";
    static final String[] CATEGORIES = {C_DEVICE, C_STEPS, C_WATCH, C_ROOMS, C_FAMILY, C_CHAT, C_REQUESTS, C_GUARDIAN};

    private OwnSyncRules() {}

    // -- keys and ids ----------------------------------------------------------------

    /** The person's own key: their offline seal key (PR #216), the same on every device they sign in on. */
    static byte[] key(String apiKey) {
        return OfflineRules.deriveKeys(apiKey)[1];
    }

    /**
     * An own item's mesh id: 8 bytes naming it under the person's key, then 8
     * bytes of MAC over those, so the person's devices tell their own ids from
     * anyone else's without opening them, and nobody else can.
     */
    static String itemId(byte[] key, String name) {
        String head = ProximityRules.hex(ProximityRules.hmac(key, "jarvis-own-id-v1|" + name), 8);
        return head + idCheck(key, head);
    }

    private static String idCheck(byte[] key, String head) {
        return ProximityRules.hex(ProximityRules.hmac(key, "jarvis-own-idcheck-v1|" + head), 8);
    }

    /** Whether a mesh id is one of this person's own (made with their key). */
    static boolean isMine(byte[] key, String id) {
        if (key == null || id == null || id.length() != 32) return false;
        return MessageDigest.isEqual(idCheck(key, id.substring(0, 16)).getBytes(StandardCharsets.US_ASCII),
                id.substring(16).getBytes(StandardCharsets.US_ASCII));
    }

    /** A device's tie-break rank, keyed so a relay cannot map it to a device id. */
    static int rank(byte[] key, String deviceId) {
        byte[] h = ProximityRules.hmac(key, "jarvis-own-rank-v1|" + deviceId);
        return ((h[0] & 0xff) << 8) | (h[1] & 0xff);
    }

    static long version(long atMs, int rank) {
        return (Math.max(0L, atMs) << RANK_BITS) | (rank & 0xffff);
    }

    static long atOf(long version) {
        return version >>> RANK_BITS;
    }

    static String kindOf(String name) {
        int colon = name == null ? -1 : name.indexOf(':');
        return colon < 0 ? (name == null ? "" : name) : name.substring(0, colon);
    }

    // -- sealing ---------------------------------------------------------------------

    /** One own item, sealed for the person's devices only. */
    static MeshRules.Item seal(byte[] key, String name, String deviceId, String deviceName, long atMs, JSONObject data,
                               long nowS, SecureRandom random) throws Exception {
        JSONObject p = new JSONObject().put("t", "own").put("k", name).put("dev", deviceId)
                .put("name", deviceName == null ? "" : deviceName).put("at", atMs).put("data", data);
        MeshRules.Item i = new MeshRules.Item(itemId(key, name), nowS + MeshRules.CACHE_TTL_S, 0, MeshRules.seal(key, p, random));
        i.type = MeshRules.T_OWN;
        i.version = version(atMs, rank(key, deviceId));
        return i;
    }

    /** An own item, opened. */
    static final class Opened {
        final String name;
        final String deviceId;
        final String deviceName;
        final long at;
        final JSONObject data;
        final long version;

        Opened(String name, String deviceId, String deviceName, long at, JSONObject data, long version) {
            this.name = name;
            this.deviceId = deviceId;
            this.deviceName = deviceName;
            this.at = at;
            this.data = data;
            this.version = version;
        }

        String kind() {
            return kindOf(name);
        }
    }

    /**
     * The item opened, or null: not an own item, not this person's, or altered
     * (the envelope must name this id, and the clear version must be the one the
     * writer sealed: its time and its device's rank).
     */
    static Opened open(byte[] key, MeshRules.Item i) {
        if (key == null || i == null || !MeshRules.T_OWN.equals(i.type)) return null;
        if (!isMine(key, i.id)) return null;
        JSONObject p = MeshRules.open(key, i.env);
        if (p == null || !"own".equals(p.optString("t"))) return null;
        String name = p.optString("k", "");
        String dev = p.optString("dev", "");
        long at = p.optLong("at", -1L);
        if (name.isEmpty() || dev.isEmpty() || at < 0 || !itemId(key, name).equals(i.id)) return null;
        if (i.version != version(at, rank(key, dev))) return null;
        JSONObject data = p.optJSONObject("data");
        return new Opened(name, dev, p.optString("name", ""), at, data == null ? new JSONObject() : data, i.version);
    }

    /**
     * Whether a store on one of the person's devices may keep an item: anything
     * that is not theirs (carried for the family), and their own only when it
     * opens as sealed. A copy whose clear version was raised on the way would
     * otherwise hold the id against every genuine update.
     */
    static boolean acceptable(byte[] key, MeshRules.Item i) {
        if (i == null || !MeshRules.T_OWN.equals(i.type) || !isMine(key, i.id)) return true;
        return open(key, i) != null;
    }

    // -- telling the two devices apart ------------------------------------------------

    /** The WHO frame: this device's id and name, sealed with the person's key and bound to the period. */
    static byte[] who(byte[] key, String deviceId, String deviceName, long period, SecureRandom random) throws Exception {
        JSONObject p = new JSONObject().put("t", "who").put("dev", deviceId).put("name", deviceName == null ? "" : deviceName)
                .put("p", period);
        return MeshRules.seal(key, p, random).toString().getBytes(StandardCharsets.UTF_8);
    }

    /** {device id, name} from a peer's WHO, or null: not this person's device, or another period's. */
    static String[] openWho(byte[] key, byte[] body, long period) {
        if (key == null || body == null) return null;
        JSONObject env;
        try {
            env = new JSONObject(new String(body, StandardCharsets.UTF_8));
        } catch (Exception e) {
            return null;
        }
        JSONObject p = MeshRules.open(key, env);
        if (p == null || !"who".equals(p.optString("t")) || Math.abs(p.optLong("p", Long.MIN_VALUE / 2) - period) > 1) {
            return null;
        }
        String dev = p.optString("dev", "");
        return dev.isEmpty() ? null : new String[] {dev, p.optString("name", "")};
    }

    /**
     * Who connects when two of a person's devices hear each other. They share a
     * name, so the family rule (whose name sorts first) cannot decide; the device
     * whose advertised mail tag is lower does, and with equal tags neither until
     * the fallback (MeshRules.FALLBACK_MS) lets either.
     */
    static boolean ownSortsFirst(int myTag, int peerTag) {
        return myTag < peerTag;
    }

    // -- steps -----------------------------------------------------------------------

    /** One device's count for one day, as that device last saw it. */
    static final class DaySteps {
        final int steps;
        final long at;
        /** Steps per local hour (index 0-23), -1 when the device kept no hour; null when none at all. */
        final int[] hours;

        DaySteps(int steps, long at, int[] hours) {
            this.steps = Math.max(0, steps);
            this.at = at;
            this.hours = hours;
        }
    }

    /** A device's days as carried: {"days": {"2026-11-25": {"steps": n, "at": ms, "hours": [...]}}}. */
    static JSONObject stepsData(Map<String, DaySteps> days) throws Exception {
        JSONObject d = new JSONObject();
        List<String> keys = new ArrayList<>(new TreeMap<>(days).keySet());
        // Only the newest days travel.
        for (int k = Math.max(0, keys.size() - STEP_DAYS); k < keys.size(); k++) {
            DaySteps s = days.get(keys.get(k));
            JSONObject o = new JSONObject().put("steps", s.steps).put("at", s.at);
            if (s.hours != null) {
                JSONArray h = new JSONArray();
                for (int v : s.hours) h.put(v);
                o.put("hours", h);
            }
            d.put(keys.get(k), o);
        }
        return new JSONObject().put("days", d);
    }

    static Map<String, DaySteps> parseSteps(JSONObject data) {
        Map<String, DaySteps> out = new TreeMap<>();
        JSONObject d = data == null ? null : data.optJSONObject("days");
        for (Iterator<String> it = d == null ? null : d.keys(); it != null && it.hasNext(); ) {
            String day = it.next();
            JSONObject o = d.optJSONObject(day);
            if (o == null || !day.matches("\\d{4}-\\d{2}-\\d{2}")) continue;
            JSONArray h = o.optJSONArray("hours");
            int[] hours = null;
            if (h != null && h.length() == 24) {
                hours = new int[24];
                for (int k = 0; k < 24; k++) hours[k] = h.optInt(k, -1);
            }
            out.put(day, new DaySteps(o.optInt("steps", 0), o.optLong("at", 0L), hours));
        }
        return out;
    }

    /**
     * A device's own count changing: a day only ever goes up (the pedometer's
     * ledger keeps the day's maximum, as geo does per source), and its updated-at
     * moves only when the count does.
     */
    static DaySteps raise(DaySteps had, int steps, int[] hours, long nowMs) {
        if (had == null) return new DaySteps(steps, nowMs, hours);
        boolean hoursChanged = hours != null && !java.util.Arrays.equals(hours, had.hours);
        if (steps <= had.steps && !hoursChanged) return had;
        return new DaySteps(Math.max(steps, had.steps), nowMs, hours != null ? hours : had.hours);
    }

    /**
     * The person's total for a day across their devices, by geo's rule
     * (services/geo/main.py {@code _fuse_day}, which POST /api/geo/steps feeds
     * and which keeps each source's daily maximum "so the phone and the watch
     * never double-count"): each hour at the most any one device counted in it,
     * summed, but never below the best single device's day total. Two devices
     * carried on the same walk count it once; a phone's morning and a watch's
     * afternoon both count.
     */
    static int dayTotal(Collection<Map<String, DaySteps>> devices, String day) {
        int best = 0;
        int[] hours = new int[24];
        boolean anyHours = false;
        for (Map<String, DaySteps> d : devices) {
            DaySteps s = d == null ? null : d.get(day);
            if (s == null) continue;
            best = Math.max(best, s.steps);
            if (s.hours == null) continue;
            for (int h = 0; h < 24; h++) {
                if (s.hours[h] > hours[h]) {
                    hours[h] = s.hours[h];
                    anyHours = true;
                }
            }
        }
        int sum = 0;
        if (anyHours) for (int v : hours) sum += v;
        return Math.max(best, sum);
    }

    // -- the watch -------------------------------------------------------------------

    /**
     * The watch's stats as an own item's data, from the 6e400004 JSON a phone
     * read (steps, goal, battery, charging, synced), filed under the day the
     * phone read it.
     */
    static JSONObject watchData(JSONObject stats, String day, long readAtMs, String watchName) throws Exception {
        JSONObject o = new JSONObject().put("day", day).put("read_at", readAtMs)
                .put("steps", Math.max(0, stats.optInt("steps", 0)))
                .put("goal", Math.max(0, stats.optInt("goal", 0)))
                .put("battery", stats.optInt("battery", -1))
                .put("charging", stats.optBoolean("charging", false))
                .put("synced", stats.optLong("synced", 0L));
        if (watchName != null && !watchName.isEmpty()) o.put("watch", watchName);
        return o;
    }

    /** The watch's steps as a device's day, for the person's total. */
    static Map<String, DaySteps> watchDays(JSONObject watch) {
        Map<String, DaySteps> out = new TreeMap<>();
        if (watch == null) return out;
        String day = watch.optString("day", "");
        if (day.matches("\\d{4}-\\d{2}-\\d{2}")) {
            out.put(day, new DaySteps(watch.optInt("steps", 0), watch.optLong("read_at", 0L), null));
        }
        return out;
    }

    // -- the person's latest location ------------------------------------------------

    /** The newest fix among the person's devices' own items, or null. */
    static Opened latestFix(Collection<Opened> devices) {
        Opened best = null;
        long bestAt = Long.MIN_VALUE;
        for (Opened o : devices) {
            if (!K_DEVICE.equals(o.kind())) continue;
            JSONObject fix = o.data.optJSONObject("fix");
            if (fix == null) continue;
            long at = fix.optLong("at", 0L);
            // Newest fix; the same instant goes to the higher version, as everywhere.
            if (best == null || at > bestAt || (at == bestAt && o.version > best.version)) {
                best = o;
                bestAt = at;
            }
        }
        return best;
    }

    // -- diagnostics -----------------------------------------------------------------

    /** Which diagnostics row an item belongs to: an own item by its kind, anything else by its mesh type. */
    static String category(String meshType, String ownKind) {
        if (MeshRules.T_OWN.equals(meshType)) {
            if (K_DEVICE.equals(ownKind)) return C_DEVICE;
            if (K_STEPS.equals(ownKind)) return C_STEPS;
            if (K_WATCH.equals(ownKind)) return C_WATCH;
            if (K_ROOMS.equals(ownKind)) return C_ROOMS;
            return null;
        }
        if (MeshRules.T_CACHE.equals(meshType)) return C_FAMILY;
        if (MeshRules.T_CHAT.equals(meshType)) return C_CHAT;
        if (MeshRules.T_REQUEST.equals(meshType) || MeshRules.T_ANSWER.equals(meshType)) return C_REQUESTS;
        if (MeshRules.T_COMMAND.equals(meshType) || MeshRules.T_ACK.equals(meshType)) return C_GUARDIAN;
        return null;
    }

    /**
     * The diagnostics log after a finished sync with one of the person's other
     * devices: when, its name, and per category "updated" (this sync brought a
     * newer copy from it) or "in_sync" (both held the same afterwards).
     * {@code log} is {device id: {...}}; returns it.
     */
    static JSONObject recordSync(JSONObject log, String peerDevice, String peerName, long atMs, Set<String> updated)
            throws Exception {
        JSONObject types = new JSONObject();
        JSONObject before = log.optJSONObject(peerDevice);
        for (String c : CATEGORIES) {
            if (updated.contains(c)) {
                types.put(c, new JSONObject().put("status", "updated").put("from", peerName).put("at", atMs));
            } else {
                types.put(c, new JSONObject().put("status", "in_sync").put("at", atMs));
            }
        }
        JSONObject row = new JSONObject().put("device", peerDevice).put("name", peerName).put("last_sync_at", atMs)
                .put("types", types);
        if (before != null && before.has("first_seen_at")) row.put("first_seen_at", before.optLong("first_seen_at"));
        else row.put("first_seen_at", atMs);
        log.put(peerDevice, row);
        return log;
    }

    /** The log as the app shows it: newest sync first. */
    static JSONArray peers(JSONObject log) {
        List<JSONObject> rows = new ArrayList<>();
        for (Iterator<String> it = log.keys(); it.hasNext(); ) {
            JSONObject r = log.optJSONObject(it.next());
            if (r != null) rows.add(r);
        }
        rows.sort((a, b) -> Long.compare(b.optLong("last_sync_at"), a.optLong("last_sync_at")));
        return new JSONArray(rows);
    }

    /** Opened own items by name, the newest copy of each (what a device shows). */
    static Map<String, Opened> openAll(byte[] key, Collection<MeshRules.Item> items) {
        Map<String, Opened> out = new LinkedHashMap<>();
        for (MeshRules.Item i : items) {
            Opened o = open(key, i);
            if (o == null) continue;
            Opened had = out.get(o.name);
            if (had == null || o.version > had.version) out.put(o.name, o);
        }
        return out;
    }
}
