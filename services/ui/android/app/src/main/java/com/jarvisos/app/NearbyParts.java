package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import org.json.JSONObject;

/**
 * Settings > Nearby & offline on this phone (see {@link NearbyRules}): the
 * parts as stored, and keeping the Bluetooth LE engine running exactly while
 * one of them needs it, inside Wander's location service when that runs, or in
 * {@link NearbyService} when it does not.
 *
 * Find family nearby and the offline chat keep their switches where PRs #211
 * and #212 stored them ({@link Proximity#enabled}, {@link FamilyMesh#enabled}),
 * so a phone updated with them on keeps them on.
 */
final class NearbyParts {
    private static final String TAG = "NearbyParts";
    static final String PREFS = "jarvis_nearby_parts";
    private static final String KEY_OFFLINE = "offline";
    private static final String KEY_CAR = "car";
    private static final String KEY_MINE = "mine";

    private NearbyParts() {}

    static SharedPreferences prefs(Context c) {
        return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** A child's phone (Guardian protects it): the parent link is always on. */
    static boolean child(Context c) {
        return GuardApi.isProtected(c);
    }

    static NearbyRules.Parts parts(Context c) {
        SharedPreferences p = prefs(c);
        boolean chat = FamilyMesh.enabled(c);
        return new NearbyRules.Parts(
                Proximity.enabled(c),
                chat,
                NearbyRules.migratedOffline(p.getBoolean(KEY_OFFLINE, false), p.contains(KEY_OFFLINE), chat),
                p.getBoolean(KEY_CAR, true),
                p.getBoolean(KEY_MINE, false),
                child(c));
    }

    static void save(Context c, NearbyRules.Parts parts) {
        Proximity.setEnabled(c, parts.nearby);
        FamilyMesh.setEnabled(c, parts.chat);
        prefs(c).edit().putBoolean(KEY_OFFLINE, parts.offline).putBoolean(KEY_CAR, parts.car)
                .putBoolean(KEY_MINE, parts.mine).apply();
    }

    /** Signed out: the next person starts from the defaults (car detection on, the rest off). */
    static void reset(Context context) {
        Context c = context.getApplicationContext();
        Proximity.setEnabled(c, false);
        FamilyMesh.setEnabled(c, false);
        prefs(c).edit().clear().putBoolean(KEY_OFFLINE, false).apply();
        OwnSync.reset(c);
        ensure(c);
    }

    static boolean engine(Context c) {
        return parts(c).engine();
    }

    static boolean mesh(Context c) {
        return parts(c).mesh();
    }

    static boolean sightings(Context c) {
        return parts(c).sightings();
    }

    static boolean offline(Context c) {
        return parts(c).offline;
    }

    static boolean car(Context c) {
        return parts(c).car;
    }

    /** My devices: this person's own devices keep their data in step (OwnSync). */
    static boolean mine(Context c) {
        return parts(c).mine;
    }

    /**
     * What the server says (a child's status from the guard, or Settings read by
     * the app): saved, then the engine is started or stopped to match.
     */
    static void applyFromServer(Context context, JSONObject server) {
        if (server == null) return;
        Context c = context.getApplicationContext();
        NearbyRules.Parts before = parts(c);
        NearbyRules.Parts after = NearbyRules.merge(before, server, child(c));
        if (after.nearby == before.nearby && after.chat == before.chat && after.offline == before.offline
                && after.car == before.car && after.mine == before.mine) {
            return;
        }
        save(c, after);
        ensure(c);
    }

    static NearbyRules.Host host(Context c) {
        return NearbyRules.host(engine(c), Proximity.scanPermission(c), LocationTrackingService.running());
    }

    /**
     * Run the engine where it should run now. Called when a switch changes, a
     * permission is answered, the guard learns this is a child's phone, at boot,
     * and when Wander's location service starts or stops.
     */
    static void ensure(Context context) {
        Context c = context.getApplicationContext();
        NearbyRules.Host host = host(c);
        try {
            switch (host) {
                case LOCATION:
                    // Wander's service hosts it: no second notification.
                    NearbyService.stop(c);
                    Proximity.start(c);
                    break;
                case OWN:
                    NearbyService.start(c);
                    Proximity.start(c);
                    break;
                default:
                    NearbyService.stop(c);
                    // Proximity.apply stops advertising and scanning when no part wants them.
                    Proximity.refresh(c);
                    break;
            }
        } catch (RuntimeException e) {
            // Android may refuse a foreground service started from the background;
            // the next app open tries again.
            Log.w(TAG, "could not place the nearby engine: " + e.getMessage());
        }
        Proximity.mailChanged();
    }

    static JSONObject state(Context context) {
        Context c = context.getApplicationContext();
        JSONObject o = parts(c).toJson();
        try {
            o.put("scanPermission", Proximity.scanPermission(c))
                    .put("advertisePermission", Proximity.advertisePermission(c))
                    .put("connectPermission", FamilyMesh.connectPermission(c))
                    .put("bluetoothOn", VehicleBluetooth.bluetoothOn(c))
                    .put("host", host(c).name().toLowerCase(java.util.Locale.ROOT))
                    .put("apiLevel", android.os.Build.VERSION.SDK_INT);
        } catch (Exception ignored) {
            // Plain values: org.json does not throw here.
        }
        return o;
    }
}
