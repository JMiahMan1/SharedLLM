package com.jarvisos.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * The decisions behind telling a car apart by its Bluetooth hardware, free of
 * Android so they run in plain JVM tests ({@link VehicleBluetooth} does the
 * Android side).
 *
 * The key is always the hardware address, never the name: a head unit's name
 * is whatever someone set it to and can change, its address does not. Only a
 * stable address can be linked: a classic (BR/EDR) address, an LE public
 * address, or an LE random static one. An LE resolvable private address
 * changes every few minutes; Android only knows a device's identity address
 * once it is bonded, so only bonded devices are offered.
 *
 * The phone keeps the set of devices connected right now (from ACL connect
 * and disconnect broadcasts, seeded from the audio profiles' connected
 * devices) and, with each location fix, sends the ones that are linked to a
 * household vehicle. A device that is not linked is never sent.
 */
final class VehicleBluetoothRules {
    // BluetoothDevice.DEVICE_TYPE_*
    static final int TYPE_UNKNOWN = 0;
    static final int TYPE_CLASSIC = 1;
    static final int TYPE_LE = 2;
    static final int TYPE_DUAL = 3;
    // BluetoothDevice.ADDRESS_TYPE_* (API 34+); -1 when the phone cannot say.
    static final int ADDR_UNKNOWN = -1;
    static final int ADDR_PUBLIC = 0;
    static final int ADDR_RANDOM = 1;

    static final String KIND_CLASSIC = "classic";
    static final String KIND_LE_PUBLIC = "le_public";
    static final String KIND_LE_STATIC = "le_static";

    /** Most linked addresses sent with one fix (geo keeps eight). */
    static final int MAX_SENT = 8;
    /** A connection older than this with no disconnect seen is not trusted (a missed broadcast). */
    static final long CONNECTION_MAX_AGE_MS = 12L * 60 * 60 * 1000;
    /** The household's linked addresses are fetched again this often. */
    static final long LINKS_REFRESH_MS = 6L * 60 * 60 * 1000;
    /** An unknown device connecting fetches them again, but no more often than this. */
    static final long LINKS_REFRESH_ON_CONNECT_MS = 10L * 60 * 1000;

    static final String REASON_NO_PERMISSION =
            "Allow Nearby devices for Jarvis so it can read this device's hardware address.";
    static final String REASON_NOT_PAIRED =
            "Pair it in Android's Bluetooth settings first; only a paired device has a fixed address.";
    static final String REASON_ROTATING =
            "This device keeps changing its address for privacy, so the phone would not know it next time. "
                    + "Pair it in Android's Bluetooth settings, then link it.";

    private VehicleBluetoothRules() {}

    // -- addresses -----------------------------------------------------------------------

    /** {@code aa:bb:..} in any common spelling to {@code AA:BB:CC:DD:EE:FF}; null when it is not one. */
    static String normalise(String value) {
        if (value == null) return null;
        String raw = value.replaceAll("[\\s:.\\-]", "").toUpperCase(Locale.ROOT);
        if (!raw.matches("[0-9A-F]{12}")) return null;
        if (raw.equals("020000000000") || raw.equals("000000000000") || raw.equals("FFFFFFFFFFFF")) return null;
        StringBuilder out = new StringBuilder(17);
        for (int i = 0; i < 12; i += 2) {
            if (i > 0) out.append(':');
            out.append(raw, i, i + 2);
        }
        return out.toString();
    }

    /** For an LE random address, by its top two bits: static (11), resolvable (01), non-resolvable (00). */
    static String randomKind(String normalised) {
        int top = Integer.parseInt(normalised.substring(0, 2), 16) >> 6;
        switch (top) {
            case 3: return "static";
            case 1: return "resolvable";
            case 0: return "non_resolvable";
            default: return "reserved";
        }
    }

    /** Whether a device can be linked, as what kind, and if not, why. */
    static final class Linkable {
        final boolean ok;
        final String kind;
        final String reason;

        Linkable(boolean ok, String kind, String reason) {
            this.ok = ok;
            this.kind = kind;
            this.reason = reason;
        }
    }

    static Linkable linkable(String address, int deviceType, boolean bonded, int addressType) {
        String a = normalise(address);
        if (a == null) return new Linkable(false, null, REASON_NO_PERMISSION);
        if (!bonded) return new Linkable(false, null, REASON_NOT_PAIRED);
        if (deviceType == TYPE_CLASSIC || deviceType == TYPE_DUAL) {
            // A BR/EDR address is the device's public address.
            return new Linkable(true, KIND_CLASSIC, null);
        }
        if (deviceType == TYPE_LE) {
            if (addressType == ADDR_PUBLIC) return new Linkable(true, KIND_LE_PUBLIC, null);
            if ("static".equals(randomKind(a))) return new Linkable(true, KIND_LE_STATIC, null);
            return new Linkable(false, null, REASON_ROTATING);
        }
        // Unknown type: a bonded record keeps the device's identity address, so it is
        // stable unless it is shaped like a rotating one.
        if (addressType == ADDR_RANDOM && !"static".equals(randomKind(a))) {
            return new Linkable(false, null, REASON_ROTATING);
        }
        return new Linkable(true, KIND_CLASSIC, null);
    }

    // -- what is connected now ---------------------------------------------------------

    /** Address -> when it connected. */
    static final class Connected {
        final Map<String, Long> since = new HashMap<>();

        void connected(String address, long now) {
            String a = normalise(address);
            if (a != null && !since.containsKey(a)) since.put(a, now);
        }

        void disconnected(String address) {
            String a = normalise(address);
            if (a != null) since.remove(a);
        }

        /** What Android says is connected now replaces what the broadcasts built up. */
        void seed(Iterable<String> addresses, long now) {
            Map<String, Long> next = new HashMap<>();
            for (String address : addresses) {
                String a = normalise(address);
                if (a == null) continue;
                Long was = since.get(a);
                next.put(a, was != null ? was : now);
            }
            since.clear();
            since.putAll(next);
        }

        /** The devices connected now, leaving out one held past CONNECTION_MAX_AGE_MS with no disconnect. */
        Set<String> active(long now) {
            Set<String> out = new TreeSet<>();
            for (Map.Entry<String, Long> e : since.entrySet()) {
                long at = e.getValue();
                if (at <= now && now - at <= CONNECTION_MAX_AGE_MS) out.add(e.getKey());
            }
            return out;
        }

        String encode() {
            List<String> parts = new ArrayList<>();
            for (Map.Entry<String, Long> e : since.entrySet()) parts.add(e.getKey() + "@" + e.getValue());
            Collections.sort(parts);
            return String.join(";", parts);
        }

        static Connected decode(String text) {
            Connected c = new Connected();
            if (text == null || text.isEmpty()) return c;
            for (String part : text.split(";")) {
                int at = part.indexOf('@');
                if (at <= 0) continue;
                String a = normalise(part.substring(0, at));
                try {
                    if (a != null) c.since.put(a, Long.parseLong(part.substring(at + 1)));
                } catch (NumberFormatException ignored) {
                    // A damaged entry is dropped; the next broadcast or seed restores it.
                }
            }
            return c;
        }
    }

    /** The connected devices that are linked to a vehicle, as sent with a fix (sorted, at most MAX_SENT). */
    static List<String> linkedConnected(Set<String> connected, Set<String> linked) {
        List<String> out = new ArrayList<>();
        for (String a : new TreeSet<>(connected)) {
            if (linked.contains(a)) out.add(a);
            if (out.size() >= MAX_SENT) break;
        }
        return out;
    }

    // -- the household's links -----------------------------------------------------------

    /** Every linked address in a GET /api/geo/vehicles answer. */
    static Set<String> linkedAddresses(JSONObject vehiclesAnswer) {
        Set<String> out = new LinkedHashSet<>();
        JSONArray vehicles = vehiclesAnswer == null ? null : vehiclesAnswer.optJSONArray("vehicles");
        if (vehicles == null) return out;
        for (int i = 0; i < vehicles.length(); i++) {
            JSONObject v = vehicles.optJSONObject(i);
            JSONArray links = v == null ? null : v.optJSONArray("bluetooth");
            if (links == null) continue;
            for (int j = 0; j < links.length(); j++) {
                JSONObject link = links.optJSONObject(j);
                String a = link == null ? null : normalise(link.optString("address", null));
                if (a != null) out.add(a);
            }
        }
        return out;
    }

    /** Which vehicle (its name) each linked address belongs to, for the device list. */
    static Map<String, String> linkedVehicleNames(JSONObject vehiclesAnswer) {
        Map<String, String> out = new HashMap<>();
        JSONArray vehicles = vehiclesAnswer == null ? null : vehiclesAnswer.optJSONArray("vehicles");
        if (vehicles == null) return out;
        for (int i = 0; i < vehicles.length(); i++) {
            JSONObject v = vehicles.optJSONObject(i);
            JSONArray links = v == null ? null : v.optJSONArray("bluetooth");
            if (links == null) continue;
            for (int j = 0; j < links.length(); j++) {
                JSONObject link = links.optJSONObject(j);
                String a = link == null ? null : normalise(link.optString("address", null));
                if (a != null) out.put(a, v.optString("name", ""));
            }
        }
        return out;
    }

    static String encodeSet(Set<String> addresses) {
        return String.join(",", new TreeSet<>(addresses));
    }

    static Set<String> decodeSet(String text) {
        Set<String> out = new TreeSet<>();
        if (text == null || text.isEmpty()) return out;
        for (String part : text.split(",")) {
            String a = normalise(part);
            if (a != null) out.add(a);
        }
        return out;
    }

    /**
     * Whether to fetch the household's links again: on the schedule, or when a
     * device that is not linked connects (it may have just been linked from
     * another phone), rate-limited so a headset reconnecting all day costs little.
     */
    static boolean refreshDue(long lastAt, long now, boolean unknownConnected) {
        if (lastAt <= 0 || now < lastAt) return true;
        long age = now - lastAt;
        if (age >= LINKS_REFRESH_MS) return true;
        return unknownConnected && age >= LINKS_REFRESH_ON_CONNECT_MS;
    }
}
