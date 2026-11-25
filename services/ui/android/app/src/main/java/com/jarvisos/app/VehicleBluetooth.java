package com.jarvisos.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.util.Log;

import com.jarvisos.app.widgets.WidgetApi;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Which linked vehicle's Bluetooth the phone is connected to, for Wander's
 * trips (see {@link VehicleBluetoothRules} for the decisions).
 *
 * The connected set is kept from ACL connect and disconnect broadcasts
 * ({@link VehicleBluetoothReceiver}, a manifest receiver Android delivers even
 * while the app is not running) and seeded from the audio profiles' connected
 * devices when the app or the location service starts. Each location fix
 * carries the linked ones ({@link #linkedConnectedNow}); nothing is sent for a
 * device that is not linked to a household vehicle, and never a name.
 *
 * Needs Android 12's Nearby devices permission (BLUETOOTH_CONNECT) to read a
 * device's address; nothing broader. Without it the fix carries nothing and
 * the health report says the permission is missing.
 */
final class VehicleBluetooth {
    private static final String TAG = "VehicleBluetooth";
    static final String PREFS = "jarvis_vehicle_bt";
    private static final String KEY_CONNECTED = "connected";
    private static final String KEY_LINKED = "linked";
    private static final String KEY_LINKED_NAMES = "linked_names";
    private static final String KEY_LINKS_AT = "links_at";
    private static final Object LOCK = new Object();
    private static volatile boolean refreshing;

    private VehicleBluetooth() {}

    static SharedPreferences prefs(Context c) {
        return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean permissionGranted(Context c) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true;
        return c.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED;
    }

    static BluetoothAdapter adapter(Context c) {
        BluetoothManager bm = (BluetoothManager) c.getSystemService(Context.BLUETOOTH_SERVICE);
        return bm == null ? null : bm.getAdapter();
    }

    static boolean bluetoothOn(Context c) {
        BluetoothAdapter a = adapter(c);
        try {
            return a != null && a.isEnabled();
        } catch (SecurityException e) {
            return false;
        }
    }

    // -- the connected set ---------------------------------------------------------------

    private static VehicleBluetoothRules.Connected load(Context c) {
        return VehicleBluetoothRules.Connected.decode(prefs(c).getString(KEY_CONNECTED, ""));
    }

    private static void save(Context c, VehicleBluetoothRules.Connected connected) {
        prefs(c).edit().putString(KEY_CONNECTED, connected.encode()).apply();
    }

    static Set<String> linked(Context c) {
        return VehicleBluetoothRules.decodeSet(prefs(c).getString(KEY_LINKED, ""));
    }

    /** A connect or disconnect broadcast (or Bluetooth switched off). */
    static void onBroadcast(Context context, Intent intent) {
        Context c = context.getApplicationContext();
        String action = intent == null ? null : intent.getAction();
        if (action == null) return;
        long now = System.currentTimeMillis();
        boolean unknownConnected = false;
        synchronized (LOCK) {
            VehicleBluetoothRules.Connected connected = load(c);
            if (BluetoothAdapter.ACTION_STATE_CHANGED.equals(action)) {
                int s = intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR);
                if (s != BluetoothAdapter.STATE_OFF) return;
                connected.since.clear();
            } else {
                String address = addressOf(intent);
                if (address == null) return;
                if (BluetoothDevice.ACTION_ACL_CONNECTED.equals(action)) {
                    connected.connected(address, now);
                    String a = VehicleBluetoothRules.normalise(address);
                    unknownConnected = a != null && !linked(c).contains(a);
                } else if (BluetoothDevice.ACTION_ACL_DISCONNECTED.equals(action)) {
                    connected.disconnected(address);
                } else {
                    return;
                }
            }
            save(c, connected);
        }
        refreshLinksIfDue(c, unknownConnected);
    }

    @SuppressWarnings("deprecation")
    private static String addressOf(Intent intent) {
        BluetoothDevice device;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice.class);
        } else {
            device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
        }
        try {
            return device == null ? null : device.getAddress();
        } catch (SecurityException e) {
            return null;
        }
    }

    /**
     * Ask the audio profiles which devices are connected now and take that as
     * the truth for them (a disconnect broadcast missed while the phone was
     * off would otherwise keep a car "connected" until the age limit).
     */
    static void seed(Context context) {
        final Context c = context.getApplicationContext();
        if (!permissionGranted(c)) return;
        BluetoothAdapter a = adapter(c);
        if (a == null || !bluetoothOn(c)) {
            synchronized (LOCK) {
                VehicleBluetoothRules.Connected connected = load(c);
                connected.since.clear();
                save(c, connected);
            }
            return;
        }
        final int[] profiles = {BluetoothProfile.HEADSET, BluetoothProfile.A2DP};
        final List<String> seen = new ArrayList<>();
        final int[] pending = {profiles.length};
        for (int profile : profiles) {
            boolean asked;
            try {
                asked = a.getProfileProxy(c, new BluetoothProfile.ServiceListener() {
                    @Override
                    public void onServiceConnected(int p, BluetoothProfile proxy) {
                        try {
                            for (BluetoothDevice d : proxy.getConnectedDevices()) seen.add(d.getAddress());
                        } catch (SecurityException e) {
                            Log.i(TAG, "profile " + p + ": " + e.getMessage());
                        } finally {
                            try {
                                a.closeProfileProxy(p, proxy);
                            } catch (RuntimeException ignored) {
                                // Closing a proxy the stack already dropped.
                            }
                            done(c, seen, pending);
                        }
                    }

                    @Override
                    public void onServiceDisconnected(int p) {
                        // Nothing held between calls.
                    }
                }, profile);
            } catch (SecurityException e) {
                asked = false;
            }
            if (!asked) {
                // One profile unavailable: the broadcasts stay the truth; no seed.
                return;
            }
        }
    }

    private static void done(Context c, List<String> seen, int[] pending) {
        synchronized (LOCK) {
            pending[0]--;
            if (pending[0] > 0) return;
            VehicleBluetoothRules.Connected connected = load(c);
            connected.seed(seen, System.currentTimeMillis());
            save(c, connected);
        }
    }

    /** The connected devices linked to a household vehicle, for a location fix. */
    static List<String> linkedConnectedNow(Context context) {
        Context c = context.getApplicationContext();
        if (!permissionGranted(c)) return new ArrayList<>();
        Set<String> active;
        synchronized (LOCK) {
            active = load(c).active(System.currentTimeMillis());
        }
        refreshLinksIfDue(c, false);
        return VehicleBluetoothRules.linkedConnected(active, linked(c));
    }

    static Set<String> connectedNow(Context context) {
        synchronized (LOCK) {
            return load(context.getApplicationContext()).active(System.currentTimeMillis());
        }
    }

    // -- the household's links ---------------------------------------------------------

    static void refreshLinksIfDue(Context c, boolean unknownConnected) {
        long last = prefs(c).getLong(KEY_LINKS_AT, 0L);
        if (!VehicleBluetoothRules.refreshDue(last, System.currentTimeMillis(), unknownConnected)) return;
        refreshLinksAsync(c);
    }

    static void refreshLinksAsync(Context context) {
        final Context c = context.getApplicationContext();
        if (refreshing) return;
        refreshing = true;
        try {
            new Thread(() -> {
                try {
                    refreshLinks(c);
                } finally {
                    refreshing = false;
                }
            }, "vehicle-bt-links").start();
        } catch (RuntimeException e) {
            refreshing = false;
            Log.w(TAG, "could not start the links refresh", e);
        }
    }

    /** Fetch the household's vehicles and keep their linked addresses (blocks: never the main thread). */
    static void refreshLinks(Context c) {
        try {
            WidgetApi.ensureCredentials(c);
            String key = WidgetApi.apiKey(c);
            if (key == null || key.isEmpty()) return;
            JSONObject answer = WidgetApi.get(c, "/api/geo/vehicles");
            remember(c, answer);
        } catch (Exception e) {
            // Offline or an older server: keep the last list, try again on the next fix.
            prefs(c).edit().putLong(KEY_LINKS_AT, System.currentTimeMillis()
                    - VehicleBluetoothRules.LINKS_REFRESH_MS + VehicleBluetoothRules.LINKS_REFRESH_ON_CONNECT_MS).apply();
            Log.i(TAG, "links not refreshed: " + e.getMessage());
        }
    }

    /** Keep a GET /api/geo/vehicles answer's links (the web app passes the one it just read too). */
    static void remember(Context c, JSONObject answer) {
        Set<String> linked = VehicleBluetoothRules.linkedAddresses(answer);
        Map<String, String> names = VehicleBluetoothRules.linkedVehicleNames(answer);
        prefs(c).edit()
                .putString(KEY_LINKED, VehicleBluetoothRules.encodeSet(linked))
                .putString(KEY_LINKED_NAMES, new JSONObject(names).toString())
                .putLong(KEY_LINKS_AT, System.currentTimeMillis())
                .apply();
    }

    /** When the household's links were last read (0 = never). */
    static long linksAt(Context c) {
        return prefs(c).getLong(KEY_LINKS_AT, 0L);
    }

    static Map<String, String> linkedNames(Context c) {
        Map<String, String> out = new HashMap<>();
        try {
            JSONObject o = new JSONObject(prefs(c).getString(KEY_LINKED_NAMES, "{}"));
            for (java.util.Iterator<String> it = o.keys(); it.hasNext(); ) {
                String k = it.next();
                out.put(k, o.optString(k, ""));
            }
        } catch (Exception ignored) {
            // A damaged cache is just empty until the next refresh.
        }
        return out;
    }

    // -- the device list the web app shows ---------------------------------------------

    /** One bonded device as the link picker shows it. */
    static final class Bonded {
        String name;
        String address;
        int type;
        boolean connected;
        VehicleBluetoothRules.Linkable linkable;
        String linkedTo;
    }

    @SuppressLint("MissingPermission")
    static List<Bonded> bonded(Context context) {
        Context c = context.getApplicationContext();
        List<Bonded> out = new ArrayList<>();
        BluetoothAdapter a = adapter(c);
        if (a == null || !permissionGranted(c)) return out;
        Set<BluetoothDevice> devices;
        try {
            devices = a.getBondedDevices();
        } catch (SecurityException e) {
            return out;
        }
        if (devices == null) return out;
        Set<String> connected = connectedNow(c);
        Map<String, String> names = linkedNames(c);
        for (BluetoothDevice d : devices) {
            Bonded b = new Bonded();
            try {
                b.name = d.getName();
                b.address = d.getAddress();
                b.type = d.getType();
            } catch (SecurityException e) {
                continue;
            }
            int addressType = VehicleBluetoothRules.ADDR_UNKNOWN;
            if (Build.VERSION.SDK_INT >= 35) {
                int t = d.getAddressType();
                if (t == BluetoothDevice.ADDRESS_TYPE_PUBLIC) addressType = VehicleBluetoothRules.ADDR_PUBLIC;
                else if (t == BluetoothDevice.ADDRESS_TYPE_RANDOM) addressType = VehicleBluetoothRules.ADDR_RANDOM;
            }
            b.linkable = VehicleBluetoothRules.linkable(b.address, b.type, true, addressType);
            String normal = VehicleBluetoothRules.normalise(b.address);
            b.connected = normal != null && connected.contains(normal);
            b.linkedTo = normal == null ? null : names.get(normal);
            if (normal != null) b.address = normal;
            out.add(b);
        }
        return out;
    }
}
