package com.jarvisos.app;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Settings > Nearby & offline, the rules with no Android in them (so the JVM
 * tests cover them): which parts are on, what the Bluetooth LE engine must run
 * for them, where it runs, and which permissions a part needs.
 *
 * The parts: {@code nearby} (Find family nearby), {@code chat} (offline family
 * chat), {@code offline} (offline mode: Voice Assistant requests and shared
 * information through nearby phones), {@code car} (car detection) and
 * {@code mine} (my devices: a person's own phone and tablet keep their data in
 * step, see {@link OwnSyncRules}). On a
 * child's phone the parent link (a parent's offline lock, unlock and extra
 * time, and the Bluetooth-off reports) is always on, whatever the switches.
 */
final class NearbyRules {
    static final String NEARBY = "nearby";
    static final String CHAT = "chat";
    static final String OFFLINE = "offline";
    static final String CAR = "car";
    static final String MINE = "mine";
    static final String[] PARTS = {NEARBY, CHAT, OFFLINE, CAR, MINE};

    /** Who hosts the Bluetooth LE engine. */
    enum Host { NONE, LOCATION, OWN }

    private NearbyRules() {}

    /** One phone's parts, as chosen (or as a parent chose them for a child). */
    static final class Parts {
        final boolean nearby;
        final boolean chat;
        final boolean offline;
        final boolean car;
        /** My devices: this person's own devices sync their data with each other. */
        final boolean mine;
        /** A child's phone: the parent link is always on. */
        final boolean child;

        Parts(boolean nearby, boolean chat, boolean offline, boolean car, boolean child) {
            this(nearby, chat, offline, car, false, child);
        }

        Parts(boolean nearby, boolean chat, boolean offline, boolean car, boolean mine, boolean child) {
            this.nearby = nearby;
            this.chat = chat;
            this.offline = offline;
            this.car = car;
            this.mine = mine;
            this.child = child;
        }

        /** Advertise and scan (the radio every part but the car rides on). */
        boolean engine() {
            return nearby || chat || offline || mine || child;
        }

        /** The short phone-to-phone syncs (GATT): the chat, offline mode, my devices and the parent link. */
        boolean mesh() {
            return chat || offline || mine || child;
        }

        /** Record who was near and send it to Wander: only Find family nearby. */
        boolean sightings() {
            return nearby;
        }

        /** Fetch the household's latest information for offline mode. */
        boolean snapshot() {
            return offline;
        }

        JSONObject toJson() {
            JSONObject o = new JSONObject();
            try {
                o.put(NEARBY, nearby).put(CHAT, chat).put(OFFLINE, offline).put(CAR, car).put(MINE, mine)
                        .put("parentLink", child);
            } catch (Exception ignored) {
                // Plain values: org.json does not throw here.
            }
            return o;
        }
    }

    /**
     * Where the engine runs: inside Wander's location service when that is
     * running (no second notification), otherwise a small service of its own,
     * and nowhere when no part wants it or scanning is not allowed.
     */
    static Host host(boolean engineWanted, boolean scanAllowed, boolean locationServiceRunning) {
        if (!engineWanted || !scanAllowed) return Host.NONE;
        return locationServiceRunning ? Host.LOCATION : Host.OWN;
    }

    /**
     * The Android 12+ permissions to ask for when the parts change from
     * {@code before} to {@code after}: only for a part that has just been
     * turned on, and only what that part needs (scan and advertise for the
     * radio, connect for the phone-to-phone syncs and for car detection).
     * Nothing on Android 9 to 11, where scanning uses the location permission.
     */
    static List<String> toAsk(Parts before, Parts after, int sdk, boolean scanGranted, boolean advertiseGranted,
                              boolean connectGranted) {
        List<String> out = new ArrayList<>();
        if (sdk < 31) return out;
        boolean newRadio = after.engine() && (!before.engine() || (after.nearby && !before.nearby)
                || (after.chat && !before.chat) || (after.offline && !before.offline) || (after.mine && !before.mine));
        if (newRadio && !scanGranted) out.add("scan");
        if (newRadio && !advertiseGranted) out.add("advertise");
        boolean newConnect = (after.mesh() && !before.mesh()) || (after.chat && !before.chat)
                || (after.offline && !before.offline) || (after.mine && !before.mine) || (after.car && !before.car);
        if (newConnect && !connectGranted) out.add("connect");
        return out;
    }

    /**
     * The parts the server sent (Settings for a grown member, a parent's choice
     * for a child), over what the phone had: a missing or non-boolean value
     * keeps the phone's own.
     */
    static Parts merge(Parts current, JSONObject server, boolean child) {
        if (server == null) return new Parts(current.nearby, current.chat, current.offline, current.car, current.mine, child);
        return new Parts(
                bool(server, NEARBY, current.nearby),
                bool(server, CHAT, current.chat),
                bool(server, OFFLINE, current.offline),
                bool(server, CAR, current.car),
                bool(server, MINE, current.mine),
                child);
    }

    private static boolean bool(JSONObject o, String key, boolean fallback) {
        Object v = o.opt(key);
        return v instanceof Boolean ? (Boolean) v : fallback;
    }

    /**
     * Before these settings, offline mode had no switch of its own: it ran
     * whenever the offline chat did. A phone with no stored choice keeps that.
     */
    static boolean migratedOffline(boolean stored, boolean hasStored, boolean chatWasOn) {
        return hasStored ? stored : chatWasOn;
    }
}
