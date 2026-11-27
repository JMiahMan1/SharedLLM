package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.security.SecureRandom;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * A person's own devices keeping their data in step over Bluetooth LE (see
 * {@link OwnSyncRules} for the rules). Settings > Nearby & offline, "My
 * devices", turns it on; it rides on {@link FamilyMesh}'s short GATT syncs and
 * store, so it adds no radio of its own.
 *
 * This device publishes its own items into the mesh store: its name, battery and
 * last location fix, its step counts per day, the Jarvis Watch's last stats when
 * it read the watch, and the offline chat rooms it fetched. A sync with another
 * device of the same person (heard as {@code ownDevice}) exchanges them, newest
 * winning, along with everything the mesh already carries for the person (the
 * household snapshot, chat messages, queued requests, parent commands).
 */
final class OwnSync {
    private static final String TAG = "OwnSync";
    static final String PREFS = "jarvis_own_sync";
    private static final String KEY_FIX = "fix";
    private static final String KEY_STEPS = "steps";
    private static final String KEY_LOG = "log";
    private static final String KEY_DEVICE_AT = "device_published_at";
    private static final String KEY_STEPS_AT = "steps_published_at";
    private static final String KEY_WATCH_AT = "watch_published_at";
    /** One device item at most this often (a fix every few minutes is fresh enough offline). */
    static final long DEVICE_EVERY_MS = 5 * 60_000L;
    static final long STEPS_EVERY_MS = 5 * 60_000L;
    static final long WATCH_EVERY_MS = 5 * 60_000L;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Object LOCK = new Object();

    private OwnSync() {}

    static SharedPreferences prefs(Context c) {
        return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** My devices is on and the phone-to-phone syncs can run. */
    static boolean enabled(Context c) {
        return NearbyParts.mine(c) && FamilyMesh.active(c);
    }

    /** The person's own key (from their API key), or null when signed out. */
    static byte[] key(Context c) {
        String apiKey = com.jarvisos.app.widgets.WidgetApi.apiKey(c);
        return apiKey == null || apiKey.isEmpty() ? null : OwnSyncRules.key(apiKey);
    }

    static String deviceId(Context c) {
        return PushGateway.deviceId(c);
    }

    static String deviceName() {
        return LocationTrackingService.deviceName();
    }

    /** The store's check: this person's own items must open (see OwnSyncRules#acceptable). */
    static MeshRules.Acceptor acceptor(Context context) {
        Context c = context.getApplicationContext();
        byte[][] k = new byte[1][];
        boolean[] read = {false};
        return item -> {
            if (!MeshRules.T_OWN.equals(item.type)) return true;
            if (!read[0]) {
                k[0] = key(c);
                read[0] = true;
            }
            return k[0] == null || OwnSyncRules.acceptable(k[0], item);
        };
    }

    /** Let a sync say who this device is (and learn who the other one is), when My devices is on. */
    static void prepare(Context c, MeshRules.Session s, boolean ask) {
        if (!enabled(c)) return;
        byte[] k = key(c);
        if (k == null) return;
        s.setOwn(k, deviceId(c), deviceName(), ask, RANDOM);
    }

    // -- what this device publishes -----------------------------------------------------

    /** Location tracking took a fix (LocationTrackingService): keep it and share it. */
    static void noteFix(Context context, double lat, double lon, double accuracy, long atMs, int battery, Boolean charging) {
        Context c = context.getApplicationContext();
        try {
            JSONObject fix = new JSONObject().put("lat", lat).put("lon", lon).put("at", atMs);
            if (!Double.isNaN(accuracy)) fix.put("acc", Math.round(accuracy));
            JSONObject o = new JSONObject().put("fix", fix).put("noted_at", System.currentTimeMillis());
            if (battery >= 0) o.put("battery", battery);
            if (charging != null) o.put("charging", charging);
            prefs(c).edit().putString(KEY_FIX, o.toString()).apply();
        } catch (Exception e) {
            return;
        }
        publish(c, false);
    }

    /** The watch's stats a phone read (6e400004), as an item for the person's other devices. */
    static void noteWatch(Context context, JSONObject stats, String watchName) throws Exception {
        Context c = context.getApplicationContext();
        if (!enabled(c)) return;
        byte[] k = key(c);
        if (k == null) return;
        long now = System.currentTimeMillis();
        SharedPreferences p = prefs(c);
        JSONObject data = OwnSyncRules.watchData(stats, LocalDate.now().toString(), now, watchName);
        Map<String, OwnSyncRules.Opened> mine = opened(c, k);
        OwnSyncRules.Opened had = mine.get(OwnSyncRules.K_WATCH);
        boolean changed = had == null || had.data.optInt("steps") != data.optInt("steps")
                || had.data.optInt("battery") != data.optInt("battery")
                || !had.data.optString("day").equals(data.optString("day"));
        if (!changed && now - p.getLong(KEY_WATCH_AT, 0L) < WATCH_EVERY_MS) return;
        add(c, OwnSyncRules.seal(k, OwnSyncRules.K_WATCH, deviceId(c), deviceName(), now, data, now / 1000L, RANDOM));
        p.edit().putLong(KEY_WATCH_AT, now).apply();
    }

    /** The offline chat rooms were fetched (FamilyMesh.refreshRooms): the person's other devices get them too. */
    static void noteRooms(Context context, JSONObject answer, long atMs) {
        Context c = context.getApplicationContext();
        if (!enabled(c)) return;
        byte[] k = key(c);
        if (k == null || answer == null) return;
        try {
            add(c, OwnSyncRules.seal(k, OwnSyncRules.K_ROOMS, deviceId(c), deviceName(), atMs,
                    new JSONObject().put("answer", answer), atMs / 1000L, RANDOM));
        } catch (Exception e) {
            Log.i(TAG, "rooms not shared: " + e.getMessage());
        }
    }

    /**
     * Put this device's own items in the store: its device item (name, battery,
     * last fix) and its step days, each when changed (and not more often than a
     * few minutes apart unless {@code force}). Proximity calls it on every flush.
     */
    static void publish(Context context, boolean force) {
        Context c = context.getApplicationContext();
        if (!enabled(c)) return;
        byte[] k = key(c);
        if (k == null) return;
        long now = System.currentTimeMillis();
        SharedPreferences p = prefs(c);
        String me = deviceId(c);
        List<MeshRules.Item> out = new ArrayList<>();
        try {
            synchronized (LOCK) {
                JSONObject fix = new JSONObject(p.getString(KEY_FIX, "{}"));
                long fixAt = fix.optJSONObject("fix") == null ? 0L : fix.getJSONObject("fix").optLong("at");
                long devAt = Math.max(fixAt, fix.optLong("noted_at", 0L));
                OwnSyncRules.Opened had = opened(c, k).get(OwnSyncRules.K_DEVICE + ":" + me);
                if (devAt > 0 && (had == null || had.at < devAt)
                        && (force || now - p.getLong(KEY_DEVICE_AT, 0L) >= DEVICE_EVERY_MS || had == null)) {
                    JSONObject data = new JSONObject().put("name", deviceName());
                    if (fix.optJSONObject("fix") != null) data.put("fix", fix.getJSONObject("fix"));
                    if (fix.has("battery")) data.put("battery", fix.optInt("battery"));
                    if (fix.has("charging")) data.put("charging", fix.optBoolean("charging"));
                    out.add(OwnSyncRules.seal(k, OwnSyncRules.K_DEVICE + ":" + me, me, deviceName(), devAt, data,
                            now / 1000L, RANDOM));
                    p.edit().putLong(KEY_DEVICE_AT, now).apply();
                }
                if (force || now - p.getLong(KEY_STEPS_AT, 0L) >= STEPS_EVERY_MS) {
                    Map<String, OwnSyncRules.DaySteps> days = ownSteps(c, now);
                    long at = 0L;
                    for (OwnSyncRules.DaySteps d : days.values()) at = Math.max(at, d.at);
                    OwnSyncRules.Opened was = opened(c, k).get(OwnSyncRules.K_STEPS + ":" + me);
                    if (at > 0 && (was == null || was.at < at)) {
                        out.add(OwnSyncRules.seal(k, OwnSyncRules.K_STEPS + ":" + me, me, deviceName(), at,
                                OwnSyncRules.stepsData(days), now / 1000L, RANDOM));
                    }
                    p.edit().putLong(KEY_STEPS_AT, now).apply();
                }
            }
        } catch (Exception e) {
            Log.i(TAG, "own items not published: " + e.getMessage());
        }
        for (MeshRules.Item i : out) add(c, i);
    }

    /**
     * This device's own counts per day (the pedometer's ledger, which keeps each
     * day's maximum), each with when it last went up. Kept here too so the
     * updated-at survives and never moves without a change.
     */
    static Map<String, OwnSyncRules.DaySteps> ownSteps(Context c, long nowMs) throws Exception {
        SharedPreferences p = prefs(c);
        Map<String, OwnSyncRules.DaySteps> kept = OwnSyncRules.parseSteps(new JSONObject(p.getString(KEY_STEPS, "{}")));
        Map<String, OwnSyncRules.DaySteps> out = new TreeMap<>();
        if (!StepSyncService.isStepsEnabled(c)) return kept;
        StepLedger ledger = new StepLedger(c);
        for (StepLedger.DayEntry d : ledger.history(OwnSyncRules.STEP_DAYS, StepLedger.SOURCE_PHONE)) {
            List<StepLedger.HourEntry> hours = ledger.hoursFor(d.day, StepLedger.SOURCE_PHONE);
            int[] h = null;
            if (!hours.isEmpty()) {
                h = new int[24];
                for (StepLedger.HourEntry e : hours) if (e.hour >= 0 && e.hour < 24) h[e.hour] = Math.max(0, e.steps);
            }
            out.put(d.day, OwnSyncRules.raise(kept.get(d.day), d.steps, h, nowMs));
        }
        p.edit().putString(KEY_STEPS, OwnSyncRules.stepsData(out).toString()).apply();
        return out;
    }

    private static void add(Context c, MeshRules.Item i) {
        i.receivedAtMs = System.currentTimeMillis();
        FamilyMesh.addAll(c, Collections.singletonList(i));
    }

    /** The person's own items this device holds, opened, newest of each. */
    static Map<String, OwnSyncRules.Opened> opened(Context c, byte[] k) {
        return OwnSyncRules.openAll(k, FamilyMesh.store(c).items.values());
    }

    // -- after a sync ----------------------------------------------------------------------

    /**
     * A sync ended: with one of the person's other devices (it said who it is),
     * note what came from it per kind of data; and adopt a newer room list
     * whoever carried it.
     */
    static void afterSession(Context context, MeshRules.Session s) {
        Context c = context.getApplicationContext();
        byte[] k = key(c);
        if (k == null) return;
        MeshRules.Store store = FamilyMesh.store(c);
        Set<String> updated = new HashSet<>();
        for (String id : s.receivedIds) {
            MeshRules.Item i = store.items.get(id);
            if (i == null) continue;
            OwnSyncRules.Opened o = OwnSyncRules.open(k, i);
            if (MeshRules.T_OWN.equals(i.type) && o == null) continue; // another person's, carried
            String cat = OwnSyncRules.category(i.type, o == null ? null : o.kind());
            if (cat != null) updated.add(cat);
        }
        adoptRooms(c, k);
        if (s.peerDevice == null || !s.done) return;
        synchronized (LOCK) {
            try {
                JSONObject log = new JSONObject(prefs(c).getString(KEY_LOG, "{}"));
                String name = s.peerName == null || s.peerName.isEmpty() ? "Your other device" : s.peerName;
                OwnSyncRules.recordSync(log, s.peerDevice, name, System.currentTimeMillis(), updated);
                prefs(c).edit().putString(KEY_LOG, log.toString()).apply();
            } catch (Exception e) {
                Log.i(TAG, "sync not noted: " + e.getMessage());
            }
        }
    }

    /** A room list newer than this device's own (another device of the person fetched it later): use it. */
    private static void adoptRooms(Context c, byte[] k) {
        OwnSyncRules.Opened r = opened(c, k).get(OwnSyncRules.K_ROOMS);
        if (r == null || r.deviceId.equals(deviceId(c))) return;
        JSONObject answer = r.data.optJSONObject("answer");
        if (answer != null) FamilyMesh.adoptRooms(c, answer, r.at);
    }

    // -- for the app ------------------------------------------------------------------------

    /** The person's own data as this device holds it: their devices, the latest fix, today's steps, the watch. */
    static JSONObject state(Context context) throws Exception {
        Context c = context.getApplicationContext();
        JSONObject out = new JSONObject().put("enabled", enabled(c)).put("deviceId", deviceId(c)).put("name", deviceName());
        byte[] k = key(c);
        if (k == null) return out.put("devices", new JSONArray());
        Map<String, OwnSyncRules.Opened> all = opened(c, k);
        String me = deviceId(c);
        String today = LocalDate.now().toString();
        JSONArray devices = new JSONArray();
        List<Map<String, OwnSyncRules.DaySteps>> stepDevices = new ArrayList<>();
        JSONArray stepRows = new JSONArray();
        Map<String, OwnSyncRules.DaySteps> mySteps = ownSteps(c, System.currentTimeMillis());
        stepDevices.add(mySteps);
        OwnSyncRules.DaySteps myToday = mySteps.get(today);
        stepRows.put(new JSONObject().put("device", me).put("name", deviceName()).put("me", true)
                .put("steps", myToday == null ? 0 : myToday.steps).put("at", myToday == null ? JSONObject.NULL : myToday.at));
        for (OwnSyncRules.Opened o : all.values()) {
            if (OwnSyncRules.K_DEVICE.equals(o.kind())) {
                JSONObject row = new JSONObject(o.data.toString()).put("device", o.deviceId)
                        .put("me", o.deviceId.equals(me)).put("at", o.at);
                devices.put(row);
            } else if (OwnSyncRules.K_STEPS.equals(o.kind()) && !o.deviceId.equals(me)) {
                Map<String, OwnSyncRules.DaySteps> d = OwnSyncRules.parseSteps(o.data);
                stepDevices.add(d);
                OwnSyncRules.DaySteps t = d.get(today);
                stepRows.put(new JSONObject().put("device", o.deviceId).put("name", o.deviceName).put("me", false)
                        .put("steps", t == null ? 0 : t.steps).put("at", t == null ? JSONObject.NULL : t.at));
            }
        }
        OwnSyncRules.Opened watch = all.get(OwnSyncRules.K_WATCH);
        if (watch != null) {
            stepDevices.add(OwnSyncRules.watchDays(watch.data));
            out.put("watch", new JSONObject(watch.data.toString()).put("via", watch.deviceName));
            if (today.equals(watch.data.optString("day"))) {
                stepRows.put(new JSONObject().put("device", OwnSyncRules.WATCH_DEVICE)
                        .put("name", watch.data.optString("watch", "Jarvis Watch")).put("me", false)
                        .put("steps", watch.data.optInt("steps")).put("at", watch.data.optLong("read_at")));
            }
        }
        OwnSyncRules.Opened latest = OwnSyncRules.latestFix(all.values());
        if (latest != null) {
            out.put("latestFix", new JSONObject(latest.data.getJSONObject("fix").toString())
                    .put("device", latest.deviceId).put("name", latest.deviceName));
        }
        return out.put("devices", devices)
                .put("steps", new JSONObject().put("day", today).put("total", OwnSyncRules.dayTotal(stepDevices, today))
                        .put("devices", stepRows));
    }

    /** For Bluetooth diagnostics: each of the person's other devices, when it last synced, and per kind. */
    static JSONObject diagnostics(Context context) throws Exception {
        Context c = context.getApplicationContext();
        JSONObject log;
        try {
            log = new JSONObject(prefs(c).getString(KEY_LOG, "{}"));
        } catch (Exception e) {
            log = new JSONObject();
        }
        int own = 0;
        byte[] k = key(c);
        if (k != null) own = opened(c, k).size();
        return new JSONObject().put("enabled", enabled(c)).put("turnedOn", NearbyParts.mine(c))
                .put("deviceId", deviceId(c)).put("name", deviceName()).put("ownItems", own)
                .put("peers", OwnSyncRules.peers(log));
    }

    /** Signed out: nothing of the person's stays behind. */
    static void reset(Context context) {
        prefs(context).edit().clear().apply();
    }
}
