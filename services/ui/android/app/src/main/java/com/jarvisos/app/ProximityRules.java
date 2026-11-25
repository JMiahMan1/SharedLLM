package com.jarvisos.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * The decisions behind finding the family's phones over Bluetooth LE with no
 * network, free of Android so they run in plain JVM tests ({@link Proximity}
 * does the radio).
 *
 * <h3>Discreet by construction</h3>
 * A phone advertises no name and nothing fixed: one 128-bit service UUID and
 * eight bytes of service data, both derived from the household key (HMAC-SHA256)
 * and the current period ({@link #PERIOD_S}, fifteen minutes):
 * <ul>
 *   <li>the service UUID is the household's for this period, so a family phone
 *       can filter for it in the Bluetooth chip (cheap screen-off scanning)
 *       while to anyone else it is a random UUID that changes every period;</li>
 *   <li>the data is six bytes naming the member for this period, one byte of
 *       flags and the calibrated signal at one metre.</li>
 * </ul>
 * Android already randomises the advertising address, so nothing links one
 * period to the next without the key. A family phone works out every member's
 * identifier for the previous, current and next period (clock skew) and
 * resolves what it hears on the device.
 *
 * <h3>Distance</h3>
 * Only bands, from the path loss (calibrated power at 1 m minus the received
 * signal), smoothed by the median over a short window: {@code next_to} (within
 * a couple of metres: a car, a sofa), {@code same_room}, {@code nearby}. Body,
 * pocket and walls move a reading by 10 dB or more, so metres would be fiction.
 *
 * <h3>Battery</h3>
 * Idle: advertising at Android's low-power rate (about one packet a second)
 * and a {@link #IDLE_SCAN_ON_MS} scan in every {@link #IDLE_CYCLE_MS} in
 * low-power mode with a hardware filter. While driving (a trip): balanced
 * scanning without pauses, so a shared car is noticed within seconds. Expected
 * cost, from published figures for these modes (not measured: the emulator has
 * no Bluetooth): advertising about 0.3 to 0.5% of a day's battery, idle
 * scanning about 0.3%, an hour of trip scanning about 0.2%; roughly 1% a day.
 */
final class ProximityRules {
    static final long PERIOD_S = 900;
    /** Bytes of the member identifier in the service data. */
    static final int ID_BYTES = 6;
    static final int PAYLOAD_BYTES = ID_BYTES + 2;
    /** Flag bits in the payload. */
    static final int FLAG_MOVING = 1;
    /** This phone accepts a short connection for the offline chat. */
    static final int FLAG_CAN_CONNECT = 2;
    /** This phone holds offline chat messages it can pass on. */
    static final int FLAG_HAS_MAIL = 4;
    /** The flag byte's top five bits: a tag of the offline chat messages held, so a change is seen at once. */
    static final int MAIL_TAG_SHIFT = 3;

    static int mailTagBits(long digestHash) {
        return (int) (digestHash & 0x1F) << MAIL_TAG_SHIFT;
    }

    static int mailTag(int flags) {
        return (flags & 0xff) >>> MAIL_TAG_SHIFT;
    }

    /** Path loss (dB) bands. */
    static final int NEXT_TO_MAX_DB = 10;
    static final int SAME_ROOM_MAX_DB = 20;
    static final int NEARBY_MAX_DB = 32;
    /** A phone with no calibration in its payload is taken to read this at 1 m. */
    static final int DEFAULT_TX_AT_1M = -59;

    /** One sighting window per person lasts at most this long... */
    static final long WINDOW_MS = 2 * 60 * 1000L;
    /** ...and closes after this long with no sample. */
    static final long GAP_MS = 75 * 1000L;

    static final long IDLE_SCAN_ON_MS = 10_000L;
    static final long IDLE_CYCLE_MS = 60_000L;

    static final String BAND_NEXT_TO = "next_to";
    static final String BAND_SAME_ROOM = "same_room";
    static final String BAND_NEARBY = "nearby";

    private ProximityRules() {}

    static long period(long nowMs, long periodS) {
        return Math.floorDiv(nowMs / 1000L, Math.max(60L, periodS));
    }

    /** When the period after {@code nowMs} starts (ms). */
    static long nextPeriodAtMs(long nowMs, long periodS) {
        long p = Math.max(60L, periodS);
        return (period(nowMs, p) + 1) * p * 1000L;
    }

    static byte[] hmac(byte[] key, String message) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(message.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            throw new IllegalStateException("HMAC-SHA256 is not available", e);
        }
    }

    /** The household's service UUID for a period: random-looking (a version 4 UUID) to anyone without the key. */
    static UUID serviceUuid(byte[] key, long period) {
        byte[] h = hmac(key, "jarvis-proximity-uuid-v1|" + period);
        h[6] = (byte) ((h[6] & 0x0f) | 0x40);
        h[8] = (byte) ((h[8] & 0x3f) | 0x80);
        ByteBuffer b = ByteBuffer.wrap(h, 0, 16);
        return new UUID(b.getLong(), b.getLong());
    }

    /** A member's identifier for a period. */
    static byte[] memberId(byte[] key, String member, long period) {
        return Arrays.copyOf(hmac(key, "jarvis-proximity-id-v1|" + member + "|" + period), ID_BYTES);
    }

    static byte[] payload(byte[] id, int flags, int txAt1m) {
        byte[] out = new byte[PAYLOAD_BYTES];
        System.arraycopy(id, 0, out, 0, ID_BYTES);
        out[ID_BYTES] = (byte) flags;
        out[ID_BYTES + 1] = (byte) Math.max(-127, Math.min(20, txAt1m));
        return out;
    }

    static String hex(byte[] b, int len) {
        StringBuilder s = new StringBuilder(len * 2);
        for (int i = 0; i < len; i++) s.append(String.format("%02x", b[i] & 0xff));
        return s.toString();
    }

    /** A heard advertisement, resolved. */
    static final class Heard {
        final String member;
        final int flags;
        final int txAt1m;
        /** Another device of this phone's own person (a phone never hears its own advertisement). */
        final boolean ownDevice;

        Heard(String member, int flags, int txAt1m, boolean ownDevice) {
            this.member = member;
            this.flags = flags;
            this.txAt1m = txAt1m;
            this.ownDevice = ownDevice;
        }
    }

    /** Every member's identifier around now, to resolve what is heard on the device. */
    static final class Resolver {
        final long period;
        /** The service UUIDs to filter for (previous, current, next period). */
        final List<UUID> uuids = new ArrayList<>();
        private final Map<String, String> byId = new HashMap<>();
        private final String me;

        /**
         * Every member is resolved, this phone's own person too: a phone never receives its own
         * advertisement, so the person's identifier heard here is that person's other phone or tablet,
         * which must find this one (offline chat, offline mode and the person's own data sync with it).
         */
        Resolver(byte[] key, List<String> members, String me, long period) {
            this.period = period;
            this.me = me;
            for (long p = period - 1; p <= period + 1; p++) {
                UUID u = serviceUuid(key, p);
                uuids.add(u);
                for (String m : members) {
                    if (m == null || m.isEmpty()) continue;
                    byId.put(u + "/" + hex(memberId(key, m, p), ID_BYTES), m);
                }
                if (me != null && !me.isEmpty()) byId.put(u + "/" + hex(memberId(key, me, p), ID_BYTES), me);
            }
        }

        Heard resolve(UUID uuid, byte[] data) {
            if (uuid == null || data == null || data.length < PAYLOAD_BYTES) return null;
            String member = byId.get(uuid + "/" + hex(data, ID_BYTES));
            if (member == null) return null;
            int tx = data[ID_BYTES + 1];
            return new Heard(member, data[ID_BYTES] & 0xff, tx == 0 ? DEFAULT_TX_AT_1M : tx, member.equals(me));
        }
    }

    /** The band for a reading, or null when too faint to say anything. */
    static String band(int rssi, int txAt1m) {
        int loss = txAt1m - rssi;
        if (loss <= NEXT_TO_MAX_DB) return BAND_NEXT_TO;
        if (loss <= SAME_ROOM_MAX_DB) return BAND_SAME_ROOM;
        if (loss <= NEARBY_MAX_DB) return BAND_NEARBY;
        return null;
    }

    static int median(List<Integer> values) {
        List<Integer> s = new ArrayList<>(values);
        Collections.sort(s);
        int n = s.size();
        if (n == 0) return Integer.MIN_VALUE;
        return n % 2 == 1 ? s.get(n / 2) : (s.get(n / 2 - 1) + s.get(n / 2)) / 2;
    }

    /** One finished sighting window, as uploaded. */
    static final class Sighting {
        final String person;
        final String band;
        final long firstMs;
        final long lastMs;
        final int samples;
        final int rssi;

        Sighting(String person, String band, long firstMs, long lastMs, int samples, int rssi) {
            this.person = person;
            this.band = band;
            this.firstMs = firstMs;
            this.lastMs = lastMs;
            this.samples = samples;
            this.rssi = rssi;
        }

        JSONObject toJson() throws Exception {
            return new JSONObject().put("person", person).put("band", band)
                    .put("first_seen", firstMs / 1000.0).put("last_seen", lastMs / 1000.0)
                    .put("samples", samples).put("rssi", rssi);
        }
    }

    /** Readings per person, closed into sighting windows. */
    static final class Aggregator {
        private static final class Open {
            long first;
            long last;
            int tx;
            final List<Integer> rssi = new ArrayList<>();
        }

        private final Map<String, Open> open = new LinkedHashMap<>();

        void add(String person, int rssi, int txAt1m, long nowMs) {
            Open o = open.get(person);
            if (o == null) {
                o = new Open();
                o.first = nowMs;
                open.put(person, o);
            }
            o.last = nowMs;
            o.tx = txAt1m;
            o.rssi.add(rssi);
        }

        /** The windows that have ended by {@code nowMs} (all of them when {@code everything}). */
        List<Sighting> close(long nowMs, boolean everything) {
            List<Sighting> out = new ArrayList<>();
            for (java.util.Iterator<Map.Entry<String, Open>> it = open.entrySet().iterator(); it.hasNext(); ) {
                Map.Entry<String, Open> e = it.next();
                Open o = e.getValue();
                if (!everything && nowMs - o.last < GAP_MS && nowMs - o.first < WINDOW_MS) continue;
                it.remove();
                int m = median(o.rssi);
                String band = band(m, o.tx);
                if (band != null) out.add(new Sighting(e.getKey(), band, o.first, o.last, o.rssi.size(), m));
            }
            return out;
        }

        int openCount() {
            return open.size();
        }
    }

    /** How to scan now. */
    static final class ScanPlan {
        final boolean balanced;
        final long onMs;
        /** 0: no pause. */
        final long offMs;

        ScanPlan(boolean balanced, long onMs, long offMs) {
            this.balanced = balanced;
            this.onMs = onMs;
            this.offMs = offMs;
        }
    }

    static ScanPlan scanPlan(boolean driving) {
        if (driving) return new ScanPlan(true, 5 * 60_000L, 0L);
        return new ScanPlan(false, IDLE_SCAN_ON_MS, IDLE_CYCLE_MS - IDLE_SCAN_ON_MS);
    }

    // -- what the phone can do -------------------------------------------------------

    static final String ADV_ON = "on";
    static final String ADV_OFF = "off";
    static final String ADV_UNSUPPORTED = "unsupported";
    static final String ADV_FAILED = "failed";

    /** Whether this phone can advertise at all (some tablets only scan). */
    static String advertiseCapability(boolean hasAdapter, boolean multiAdvertisementSupported, boolean advertiserPresent) {
        if (!hasAdapter) return ADV_UNSUPPORTED;
        return multiAdvertisementSupported && advertiserPresent ? ADV_ON : ADV_UNSUPPORTED;
    }

    /**
     * What the health report says about proximity: ok, or one plain outcome.
     * {@code cannot_advertise} is not a fault anyone can fix: the device sees
     * the family's phones but they do not see it.
     */
    static String[] healthOutcome(boolean enabled, boolean permission, boolean bluetoothOn, String advertising) {
        if (!enabled) return new String[] {"off", "Nothing in Settings, Nearby & offline is turned on on this device"};
        if (!permission) return new String[] {"no_permission", "Nearby devices is not allowed for Jarvis"};
        if (!bluetoothOn) return new String[] {"bluetooth_off", "Bluetooth is off"};
        if (ADV_UNSUPPORTED.equals(advertising)) {
            return new String[] {"cannot_advertise",
                "This device cannot advertise over Bluetooth LE: it sees family phones, but they do not see it"};
        }
        if (ADV_FAILED.equals(advertising)) return new String[] {"advertise_failed", "Bluetooth LE advertising failed"};
        return new String[] {"ok", ""};
    }

    // -- the config the server hands out -----------------------------------------------

    static final class Config {
        final byte[] key;
        final String keyId;
        final long periodS;
        final List<String> members;
        final String me;
        /**
         * The household chat key (a separate random key), or null from an older server: a
         * parent's offline commands to a child's phone, and offline messages held from
         * before each chat room had its own key (FamilyMesh, MeshRules.Room).
         */
        byte[] chatKey;

        Config(byte[] key, String keyId, long periodS, List<String> members, String me) {
            this.key = key;
            this.keyId = keyId;
            this.periodS = periodS;
            this.members = members;
            this.me = me;
        }
    }

    /** A GET /api/geo/proximity/config answer, or null when it is not usable. */
    static Config parseConfig(JSONObject o) {
        if (o == null) return null;
        try {
            byte[] key = java.util.Base64.getDecoder().decode(o.optString("key", ""));
            if (key.length < 16) return null;
            List<String> members = new ArrayList<>();
            JSONArray a = o.optJSONArray("members");
            for (int i = 0; a != null && i < a.length(); i++) {
                String m = a.optString(i, "").trim().toLowerCase(java.util.Locale.ROOT);
                if (!m.isEmpty() && !members.contains(m)) members.add(m);
            }
            String me = o.optString("me", "").trim().toLowerCase(java.util.Locale.ROOT);
            Config c = new Config(key, o.optString("key_id", ""), o.optLong("period_s", PERIOD_S), members, me);
            String chat = o.optString("chat_key", "");
            if (!chat.isEmpty()) {
                byte[] ck = java.util.Base64.getDecoder().decode(chat);
                if (ck.length == 32) c.chatKey = ck;
            }
            return c;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }
}
