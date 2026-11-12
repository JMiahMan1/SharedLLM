package com.jarvisos.app;

import java.util.Locale;
import java.util.Random;
import java.util.regex.Pattern;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * Room detection: the phone advertises an iBeacon that the hub's Bluetooth
 * listeners hear, and the hub works out which room the phone is in.
 *
 * This class holds every decision about the beacon that does not need Android:
 * the per-install id, the iBeacon frame, the transmit power, what state the
 * beacon is in and why, the report sent to the gateway, and when a report is
 * due. BleBeacon (the Android side) feeds it what the phone says.
 *
 * The beacon never stops quietly: whenever it cannot advertise, the state is
 * {@link State#UNAVAILABLE} with a reason a person can act on, and that state
 * is reported to the hub like any other, so the family sees
 * "Room detection unavailable: reason" instead of a phone that has gone missing.
 *
 * Plain Java with no Android types, so every case is unit-tested on the JVM.
 */
public final class BeaconRules {

    private BeaconRules() {}

    /** Apple's Bluetooth SIG company id; iBeacon frames are Apple manufacturer data. */
    public static final int APPLE_COMPANY_ID = 0x004C;

    /** Reports go at most this often when nothing changed (the hub keeps the registration). */
    public static final long REFRESH_MS = 6L * 60 * 60 * 1000;

    /** First retry after a failed report; doubles each time, capped at {@link #REFRESH_MS}. */
    public static final long RETRY_MIN_MS = 5L * 60 * 1000;

    private static final Pattern UUID_RE =
            Pattern.compile("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");

    // -- identity --------------------------------------------------------------------

    /** Whether {@code s} is a lowercase 8-4-4-4-12 UUID, the form the hub registers. */
    public static boolean isValidUuid(String s) {
        return s != null && UUID_RE.matcher(s).matches();
    }

    /**
     * A new random (version 4) UUID for this install. Made once, on the first
     * start, and kept in app storage; it changes only when the app's data is
     * cleared (which is a new install as far as the hub can tell).
     */
    public static String newUuid(Random random) {
        byte[] b = new byte[16];
        random.nextBytes(b);
        b[6] = (byte) ((b[6] & 0x0f) | 0x40); // version 4
        b[8] = (byte) ((b[8] & 0x3f) | 0x80); // IETF variant
        StringBuilder sb = new StringBuilder(36);
        for (int i = 0; i < 16; i++) {
            if (i == 4 || i == 6 || i == 8 || i == 10) sb.append('-');
            sb.append(String.format(Locale.ROOT, "%02x", b[i] & 0xff));
        }
        return sb.toString();
    }

    /**
     * A major or minor for a new install: 1..65535. Random rather than derived
     * from anything about the phone or the person, so the beacon names nobody;
     * the hub maps it to the phone through the registration.
     */
    public static int newMajorMinor(Random random) {
        return 1 + random.nextInt(0xFFFF);
    }

    public static boolean isValidMajorMinor(int v) {
        return v >= 0 && v <= 0xFFFF;
    }

    // -- transmit power ---------------------------------------------------------------

    /** The transmit power levels Android offers, by the names the web app uses. */
    public enum TxPower {
        ULTRA_LOW("ultra_low", 0, -85),
        LOW("low", 1, -75),
        MEDIUM("medium", 2, -66),
        HIGH("high", 3, -59);

        /** The name in settings, reports and the web app. */
        public final String id;
        /** AdvertiseSettings.ADVERTISE_TX_POWER_* value. */
        public final int android;
        /**
         * Nominal RSSI at 1 m (dBm) for this level, carried in the frame as the
         * iBeacon "measured power". Phones differ; the hub's room map (recorded
         * per phone) is what makes the estimate right, not this number.
         */
        public final int measuredPower;

        TxPower(String id, int android, int measuredPower) {
            this.id = id;
            this.android = android;
            this.measuredPower = measuredPower;
        }

        /** The level named {@code id}, or null for a name that is not one (never a guess). */
        public static TxPower fromId(String id) {
            if (id == null) return null;
            for (TxPower p : values()) {
                if (p.id.equals(id.trim().toLowerCase(Locale.ROOT))) return p;
            }
            return null;
        }
    }

    /** Low: the hub only needs to hear the phone in the room it is in, and it costs the least battery. */
    public static final TxPower DEFAULT_TX_POWER = TxPower.LOW;

    // -- the frame --------------------------------------------------------------------

    /**
     * The iBeacon manufacturer data (after the company id, which Android adds):
     * 0x02 0x15, the 16 UUID bytes, major and minor big-endian, measured power.
     */
    public static byte[] manufacturerData(String uuid, int major, int minor, int measuredPower) {
        if (!isValidUuid(uuid)) throw new IllegalArgumentException("not a beacon UUID: " + uuid);
        if (!isValidMajorMinor(major) || !isValidMajorMinor(minor)) {
            throw new IllegalArgumentException("major and minor must be 0..65535");
        }
        if (measuredPower < -128 || measuredPower > 127) throw new IllegalArgumentException("measured power out of range");
        byte[] out = new byte[23];
        out[0] = 0x02;
        out[1] = 0x15;
        String hex = uuid.replace("-", "");
        for (int i = 0; i < 16; i++) {
            out[2 + i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        }
        out[18] = (byte) ((major >> 8) & 0xff);
        out[19] = (byte) (major & 0xff);
        out[20] = (byte) ((minor >> 8) & 0xff);
        out[21] = (byte) (minor & 0xff);
        out[22] = (byte) measuredPower;
        return out;
    }

    // -- state --------------------------------------------------------------------------

    public enum State {
        ADVERTISING("advertising"),
        /** Waiting for the advertiser to confirm; not reported, it is a moment. */
        STARTING("starting"),
        BLUETOOTH_OFF("bluetooth_off"),
        DISABLED("disabled"),
        UNAVAILABLE("unavailable");

        public final String id;

        State(String id) {
            this.id = id;
        }
    }

    /** A state with the reason the beacon is not advertising (null when it is, or is starting). */
    public static final class Status {
        public final State state;
        public final String reason;

        Status(State state, String reason) {
            this.state = state;
            this.reason = reason;
        }

        /** What the hub keeps; a change of either is worth a report. */
        public String key() {
            return state.id + "|" + (reason == null ? "" : reason);
        }

        @Override
        public boolean equals(Object o) {
            return o instanceof Status && ((Status) o).key().equals(key());
        }

        @Override
        public int hashCode() {
            return key().hashCode();
        }

        @Override
        public String toString() {
            return key();
        }
    }

    // AdvertiseCallback.ADVERTISE_FAILED_*, mirrored so this class has no Android types.
    static final int ERR_NONE = 0;
    static final int ERR_DATA_TOO_LARGE = 1;
    static final int ERR_TOO_MANY_ADVERTISERS = 2;
    static final int ERR_ALREADY_STARTED = 3;
    static final int ERR_INTERNAL = 4;
    static final int ERR_FEATURE_UNSUPPORTED = 5;

    public static final String REASON_NO_BLUETOOTH = "this phone has no Bluetooth";
    public static final String REASON_PERMISSION =
            "Bluetooth permission (Nearby devices) not granted to Jarvis";
    public static final String REASON_BLUETOOTH_OFF = "Bluetooth is off; it starts again when Bluetooth is back on";
    public static final String REASON_CANNOT_ADVERTISE = "this phone's Bluetooth chip cannot advertise";

    /** An advertiser failure in words. */
    public static String advertiseErrorReason(int code) {
        switch (code) {
            case ERR_DATA_TOO_LARGE:
                return "the beacon did not fit in a Bluetooth advertisement";
            case ERR_TOO_MANY_ADVERTISERS:
                return "too many apps are using Bluetooth advertising on this phone";
            case ERR_INTERNAL:
                return "Bluetooth reported an internal error while starting the beacon";
            case ERR_FEATURE_UNSUPPORTED:
                return REASON_CANNOT_ADVERTISE;
            default:
                return "Bluetooth advertising failed (error " + code + ")";
        }
    }

    /**
     * The beacon's state from what the phone says, checked in the order a
     * person would fix them.
     *
     * @param enabled          room detection is on (see {@link #effectiveEnabled})
     * @param hasBluetooth     the phone has a Bluetooth adapter
     * @param permission       BLUETOOTH_ADVERTISE is granted (always true before Android 12)
     * @param bluetoothOn      the adapter is on
     * @param canAdvertise     the chip supports LE advertising (a null advertiser or no multi-advertisement)
     * @param advertiseError   the advertiser's failure code, 0 for none
     * @param started          the advertiser confirmed the start
     */
    public static Status status(boolean enabled, boolean hasBluetooth, boolean permission, boolean bluetoothOn,
                                boolean canAdvertise, int advertiseError, boolean started) {
        if (!enabled) return new Status(State.DISABLED, null);
        if (!hasBluetooth) return new Status(State.UNAVAILABLE, REASON_NO_BLUETOOTH);
        if (!permission) return new Status(State.UNAVAILABLE, REASON_PERMISSION);
        if (!bluetoothOn) return new Status(State.BLUETOOTH_OFF, REASON_BLUETOOTH_OFF);
        if (!canAdvertise) return new Status(State.UNAVAILABLE, REASON_CANNOT_ADVERTISE);
        if (advertiseError == ERR_ALREADY_STARTED) return new Status(State.ADVERTISING, null);
        if (advertiseError != ERR_NONE) return new Status(State.UNAVAILABLE, advertiseErrorReason(advertiseError));
        return started ? new Status(State.ADVERTISING, null) : new Status(State.STARTING, null);
    }

    /** Whether the advertiser should be running for this state. */
    public static boolean shouldAdvertise(boolean enabled, boolean hasBluetooth, boolean permission,
                                          boolean bluetoothOn, boolean canAdvertise) {
        return enabled && hasBluetooth && permission && bluetoothOn && canAdvertise;
    }

    // -- who may turn it off --------------------------------------------------------------

    /**
     * Whether room detection runs. A child's phone (the guard's protected
     * phone) always transmits: it is a family safety feature, like location
     * sharing, which children are not offered a way to pause. A screen-time
     * lock changes nothing here; the beacon is not an app the lock covers.
     */
    public static boolean effectiveEnabled(boolean userEnabled, boolean protectedPhone) {
        return userEnabled || protectedPhone;
    }

    /** Whether the person may switch room detection off on this phone. */
    public static boolean canDisable(boolean protectedPhone) {
        return !protectedPhone;
    }

    // -- the report ---------------------------------------------------------------------

    /**
     * The body of {@code PUT /api/phone/ble_beacon}: the beacon (so the hub can
     * register it on the phone's entry) and its state.
     */
    public static JSONObject reportBody(String uuid, int major, int minor, TxPower power, Status status,
                                        String deviceName) throws JSONException {
        if (!isValidUuid(uuid)) throw new IllegalArgumentException("not a beacon UUID: " + uuid);
        if (status.state == State.STARTING) throw new IllegalArgumentException("a starting beacon is not reported");
        JSONObject body = new JSONObject();
        body.put("uuid", uuid);
        body.put("major", major);
        body.put("minor", minor);
        body.put("tx_power", power.measuredPower);
        body.put("tx_power_level", power.id);
        body.put("state", status.state.id);
        body.put("reason", status.reason == null ? JSONObject.NULL : status.reason);
        body.put("device_name", deviceName == null || deviceName.trim().isEmpty() ? "Android" : deviceName.trim());
        return body;
    }

    /**
     * Whether to send a report now.
     *
     * @param key          what would be reported now ({@link Status#key()} plus the beacon's identity and power)
     * @param sentKey      what the last accepted report carried, or null for none
     * @param sentAt       when the last report was accepted (ms)
     * @param failures     reports that failed since the last accepted one
     * @param lastTryAt    when the last report was tried (ms)
     */
    public static boolean reportDue(String key, String sentKey, long sentAt, int failures, long lastTryAt, long now) {
        if (failures > 0 && now - lastTryAt >= 0 && now - lastTryAt < retryDelay(failures)) return false;
        if (sentKey == null || !sentKey.equals(key)) return true;
        // The clock moved back: send rather than wait for a time that may never come.
        return now - sentAt >= REFRESH_MS || now < sentAt;
    }

    /** How long to wait after {@code failures} failed reports: 5 min, 10, 20 ... up to 6 h. */
    public static long retryDelay(int failures) {
        if (failures <= 0) return 0L;
        long d = RETRY_MIN_MS;
        for (int i = 1; i < failures && d < REFRESH_MS; i++) d *= 2;
        return Math.min(d, REFRESH_MS);
    }

    /** The reported key: a new beacon id or power is a new registration even when the state is the same. */
    public static String reportKey(String uuid, int major, int minor, TxPower power, Status status) {
        return uuid + "|" + major + "|" + minor + "|" + power.id + "|" + status.key();
    }

    /**
     * What the hub said: whether it registered the beacon, and in what words.
     * A missing or malformed answer is "not registered" with the reason, never
     * a success.
     */
    public static String[] hubResult(String responseBody) {
        if (responseBody == null || responseBody.trim().isEmpty()) {
            return new String[] {"false", "the gateway sent no answer"};
        }
        try {
            JSONObject o = new JSONObject(responseBody);
            JSONObject hub = o.optJSONObject("hub");
            if (hub == null) return new String[] {"false", "the gateway's answer did not say what the hub did"};
            boolean registered = hub.optBoolean("registered", false);
            String detail = hub.optString("detail", "");
            return new String[] {Boolean.toString(registered), detail};
        } catch (JSONException e) {
            return new String[] {"false", "the gateway's answer was not JSON"};
        }
    }
}
