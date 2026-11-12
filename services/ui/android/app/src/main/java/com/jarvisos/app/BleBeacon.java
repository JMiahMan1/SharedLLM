package com.jarvisos.app;

import android.Manifest;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.bluetooth.le.AdvertiseCallback;
import android.bluetooth.le.AdvertiseData;
import android.bluetooth.le.AdvertiseSettings;
import android.bluetooth.le.BluetoothLeAdvertiser;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;

import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;

/**
 * The room-detection beacon: an iBeacon this phone advertises so the hub's
 * Bluetooth listeners can tell which room it is in.
 *
 * Legacy LE advertising is done by the Bluetooth stack once started, so no
 * foreground service of its own is needed; it lasts as long as the app's
 * process, which the location and push services keep alive. {@link #ensure}
 * is idempotent and is called from every place the process comes back: the
 * app's start, {@link BootReceiver} after a reboot or an update, and
 * LocationTrackingService's onCreate (a sticky restart after the process was
 * killed). Bluetooth switched off stops it and switched on starts it again.
 *
 * Every state, including every way it cannot run, is reported to the gateway
 * ({@code PUT /api/phone/ble_beacon}), which registers the beacon on the
 * phone's hub entry; see {@link BeaconRules} for the decisions.
 *
 * Power: ADVERTISE_MODE_LOW_POWER (one advertisement about every second),
 * not connectable, transmit power LOW unless changed in Settings.
 */
public final class BleBeacon {
    private static final String TAG = "BleBeacon";
    static final String PREFS = "jarvis_ble_beacon";
    private static final String KEY_UUID = "uuid";
    private static final String KEY_MAJOR = "major";
    private static final String KEY_MINOR = "minor";
    private static final String KEY_TX = "tx_power";
    private static final String KEY_ENABLED = "enabled";
    private static final String KEY_SENT_KEY = "sent_key";
    private static final String KEY_SENT_AT = "sent_at";
    private static final String KEY_FAILURES = "failures";
    private static final String KEY_TRY_AT = "try_at";
    private static final String KEY_HUB_REGISTERED = "hub_registered";
    private static final String KEY_HUB_DETAIL = "hub_detail";
    private static final int CONNECT_TIMEOUT_MS = 15_000;
    private static final int READ_TIMEOUT_MS = 20_000;

    /** Told whenever the state changes, for the web app's settings panel. */
    interface Listener {
        void onChange(Context context);
    }

    private static final Object LOCK = new Object();
    private static HandlerThread thread;
    private static Handler handler;
    private static boolean receiverRegistered;
    private static AdvertiseCallback callback;
    private static boolean started;
    private static int advertiseError = BeaconRules.ERR_NONE;
    private static BeaconRules.TxPower startedWith;
    private static String lastStatusKey;
    private static volatile Listener listener;

    private BleBeacon() {}

    static void setListener(Listener l) {
        listener = l;
    }

    static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    // -- identity and settings ---------------------------------------------------------

    /** The per-install beacon id, made once and kept. */
    static void ensureIdentity(Context context) {
        SharedPreferences p = prefs(context);
        if (BeaconRules.isValidUuid(p.getString(KEY_UUID, null))
                && p.contains(KEY_MAJOR) && p.contains(KEY_MINOR)) {
            return;
        }
        SecureRandom random = new SecureRandom();
        p.edit()
                .putString(KEY_UUID, BeaconRules.newUuid(random))
                .putInt(KEY_MAJOR, BeaconRules.newMajorMinor(random))
                .putInt(KEY_MINOR, BeaconRules.newMajorMinor(random))
                .apply();
    }

    static String uuid(Context c) {
        return prefs(c).getString(KEY_UUID, "");
    }

    static int major(Context c) {
        return prefs(c).getInt(KEY_MAJOR, 0);
    }

    static int minor(Context c) {
        return prefs(c).getInt(KEY_MINOR, 0);
    }

    static BeaconRules.TxPower txPower(Context c) {
        BeaconRules.TxPower p = BeaconRules.TxPower.fromId(prefs(c).getString(KEY_TX, null));
        return p == null ? BeaconRules.DEFAULT_TX_POWER : p;
    }

    static void setTxPower(Context c, BeaconRules.TxPower power) {
        prefs(c).edit().putString(KEY_TX, power.id).apply();
    }

    /** The person's choice; on unless they turned it off. */
    static boolean userEnabled(Context c) {
        return prefs(c).getBoolean(KEY_ENABLED, true);
    }

    static void setUserEnabled(Context c, boolean enabled) {
        prefs(c).edit().putBoolean(KEY_ENABLED, enabled).apply();
    }

    static boolean protectedPhone(Context c) {
        return GuardApi.isProtected(c);
    }

    static boolean enabled(Context c) {
        return BeaconRules.effectiveEnabled(userEnabled(c), protectedPhone(c));
    }

    static Boolean hubRegistered(Context c) {
        SharedPreferences p = prefs(c);
        return p.contains(KEY_HUB_REGISTERED) ? p.getBoolean(KEY_HUB_REGISTERED, false) : null;
    }

    static String hubDetail(Context c) {
        return prefs(c).getString(KEY_HUB_DETAIL, null);
    }

    // -- what the phone says -------------------------------------------------------------

    static BluetoothAdapter adapter(Context c) {
        BluetoothManager bm = (BluetoothManager) c.getSystemService(Context.BLUETOOTH_SERVICE);
        return bm == null ? null : bm.getAdapter();
    }

    static boolean permissionGranted(Context c) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true;
        return c.checkSelfPermission(Manifest.permission.BLUETOOTH_ADVERTISE) == PackageManager.PERMISSION_GRANTED;
    }

    private static boolean bluetoothOn(BluetoothAdapter a) {
        try {
            return a != null && a.isEnabled();
        } catch (SecurityException e) {
            return false;
        }
    }

    private static BluetoothLeAdvertiser advertiser(BluetoothAdapter a) {
        try {
            return a == null ? null : a.getBluetoothLeAdvertiser();
        } catch (SecurityException e) {
            return null;
        }
    }

    /** The beacon's state now. */
    static BeaconRules.Status status(Context c) {
        BluetoothAdapter a = adapter(c);
        boolean on = bluetoothOn(a);
        // The advertiser only exists while Bluetooth is on; off, the chip is not judged.
        boolean canAdvertise = !on || advertiser(a) != null;
        synchronized (LOCK) {
            return BeaconRules.status(enabled(c), a != null, permissionGranted(c), on, canAdvertise,
                    advertiseError, started);
        }
    }

    // -- running it --------------------------------------------------------------------

    /** Start or stop the beacon to match the settings and the phone; report a change. Safe to call often. */
    static void ensure(Context context) {
        final Context app = context.getApplicationContext();
        synchronized (LOCK) {
            if (thread == null) {
                thread = new HandlerThread("ble-beacon");
                thread.start();
                handler = new Handler(thread.getLooper());
            }
            if (!receiverRegistered) {
                IntentFilter f = new IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED);
                BroadcastReceiver r = new BroadcastReceiver() {
                    @Override
                    public void onReceive(Context ctx, Intent intent) {
                        int s = intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR);
                        if (s == BluetoothAdapter.STATE_ON || s == BluetoothAdapter.STATE_OFF) {
                            Log.i(TAG, "Bluetooth " + (s == BluetoothAdapter.STATE_ON ? "on" : "off"));
                            ensure(ctx);
                        }
                    }
                };
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    // A system broadcast; RECEIVER_EXPORTED is required for one from outside the app.
                    app.registerReceiver(r, f, Context.RECEIVER_EXPORTED);
                } else {
                    app.registerReceiver(r, f);
                }
                receiverRegistered = true;
            }
        }
        handler.post(() -> apply(app));
    }

    private static void apply(Context c) {
        ensureIdentity(c);
        BluetoothAdapter a = adapter(c);
        boolean on = bluetoothOn(a);
        BluetoothLeAdvertiser adv = on ? advertiser(a) : null;
        boolean want = BeaconRules.shouldAdvertise(enabled(c), a != null, permissionGranted(c), on,
                !on || adv != null);
        BeaconRules.TxPower power = txPower(c);
        synchronized (LOCK) {
            if (!on) {
                // Bluetooth off ends every advertisement; start fresh when it is back.
                callback = null;
                started = false;
                advertiseError = BeaconRules.ERR_NONE;
            }
            if (want && callback != null && startedWith != power) {
                stopLocked(adv);
            }
            if (want && callback == null) {
                startLocked(c, adv, power);
            } else if (!want && callback != null) {
                stopLocked(adv);
            }
            // A failure belongs to the attempt; once not advertising, the next start tries afresh.
            if (!want) advertiseError = BeaconRules.ERR_NONE;
        }
        onStateMaybeChanged(c);
    }

    private static void startLocked(Context c, BluetoothLeAdvertiser adv, BeaconRules.TxPower power) {
        advertiseError = BeaconRules.ERR_NONE;
        started = false;
        byte[] data;
        try {
            data = BeaconRules.manufacturerData(uuid(c), major(c), minor(c), power.measuredPower);
        } catch (IllegalArgumentException e) {
            Log.e(TAG, "beacon identity is not valid", e);
            advertiseError = BeaconRules.ERR_DATA_TOO_LARGE;
            return;
        }
        AdvertiseSettings settings = new AdvertiseSettings.Builder()
                .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_POWER)
                .setTxPowerLevel(power.android)
                .setConnectable(false)
                .setTimeout(0)
                .build();
        AdvertiseData ad = new AdvertiseData.Builder()
                .setIncludeDeviceName(false)
                .setIncludeTxPowerLevel(false)
                .addManufacturerData(BeaconRules.APPLE_COMPANY_ID, data)
                .build();
        final Context app = c.getApplicationContext();
        AdvertiseCallback cb = new AdvertiseCallback() {
            @Override
            public void onStartSuccess(AdvertiseSettings s) {
                synchronized (LOCK) {
                    if (callback != this) return;
                    started = true;
                    advertiseError = BeaconRules.ERR_NONE;
                }
                Log.i(TAG, "advertising");
                handler.post(() -> onStateMaybeChanged(app));
            }

            @Override
            public void onStartFailure(int errorCode) {
                synchronized (LOCK) {
                    if (callback != this) return;
                    started = errorCode == BeaconRules.ERR_ALREADY_STARTED;
                    advertiseError = errorCode;
                    if (errorCode != BeaconRules.ERR_ALREADY_STARTED) callback = null;
                }
                Log.w(TAG, "advertising failed: " + BeaconRules.advertiseErrorReason(errorCode));
                handler.post(() -> onStateMaybeChanged(app));
            }
        };
        callback = cb;
        startedWith = power;
        try {
            adv.startAdvertising(settings, ad, cb);
        } catch (SecurityException e) {
            Log.w(TAG, "advertising refused: " + e.getMessage());
            callback = null;
            advertiseError = BeaconRules.ERR_INTERNAL;
        } catch (IllegalStateException e) {
            // Bluetooth went off between the check and the call; the receiver will start it again.
            Log.i(TAG, "Bluetooth not ready: " + e.getMessage());
            callback = null;
        }
    }

    private static void stopLocked(BluetoothLeAdvertiser adv) {
        AdvertiseCallback cb = callback;
        callback = null;
        started = false;
        if (cb == null || adv == null) return;
        try {
            adv.stopAdvertising(cb);
        } catch (SecurityException | IllegalStateException e) {
            Log.i(TAG, "stop: " + e.getMessage());
        }
    }

    // -- reporting -----------------------------------------------------------------------

    private static final Runnable[] REFRESH = new Runnable[1];

    private static void onStateMaybeChanged(Context c) {
        BeaconRules.Status s = status(c);
        Listener l = listener;
        boolean changed;
        synchronized (LOCK) {
            changed = !s.key().equals(lastStatusKey);
            lastStatusKey = s.key();
        }
        if (changed && l != null) l.onChange(c);
        if (s.state != BeaconRules.State.STARTING) report(c, s);
        scheduleNext(c);
    }

    /** Wake again for the next refresh or retry, whichever comes first. */
    private static void scheduleNext(Context c) {
        SharedPreferences p = prefs(c);
        int failures = p.getInt(KEY_FAILURES, 0);
        long delay = failures > 0 ? BeaconRules.retryDelay(failures) : BeaconRules.REFRESH_MS;
        synchronized (LOCK) {
            if (REFRESH[0] != null) handler.removeCallbacks(REFRESH[0]);
            REFRESH[0] = () -> onStateMaybeChanged(c);
            handler.postDelayed(REFRESH[0], delay);
        }
    }

    private static void report(Context c, BeaconRules.Status s) {
        SharedPreferences p = prefs(c);
        BeaconRules.TxPower power = txPower(c);
        String key = BeaconRules.reportKey(uuid(c), major(c), minor(c), power, s);
        long now = System.currentTimeMillis();
        int failures = p.getInt(KEY_FAILURES, 0);
        if (!BeaconRules.reportDue(key, p.getString(KEY_SENT_KEY, null), p.getLong(KEY_SENT_AT, 0L),
                failures, p.getLong(KEY_TRY_AT, 0L), now)) {
            return;
        }
        SharedPreferences t = TokenBridgePlugin.prefs(c);
        String token = t.getString(TokenBridgePlugin.KEY_API_KEY, null);
        String server = t.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
        if (token == null || server == null || token.isEmpty() || server.isEmpty()) {
            // Not signed in: nothing to send it to. The next sign-in opens the app, which calls ensure().
            p.edit().putString(KEY_HUB_DETAIL, "not signed in to Jarvis").remove(KEY_HUB_REGISTERED).apply();
            return;
        }
        String base = server.endsWith("/") ? server.substring(0, server.length() - 1) : server;
        try {
            JSONObject body = BeaconRules.reportBody(uuid(c), major(c), minor(c), power, s,
                    LocationTrackingService.deviceName());
            String[] result = put(base + "/api/phone/ble_beacon", token, body);
            int status = Integer.parseInt(result[0]);
            if (status >= 200 && status < 300) {
                String[] hub = BeaconRules.hubResult(result[1]);
                p.edit().putString(KEY_SENT_KEY, key).putLong(KEY_SENT_AT, now).putInt(KEY_FAILURES, 0)
                        .putLong(KEY_TRY_AT, now).putBoolean(KEY_HUB_REGISTERED, Boolean.parseBoolean(hub[0]))
                        .putString(KEY_HUB_DETAIL, hub[1]).apply();
                Log.i(TAG, "reported " + s.state.id + "; hub registered=" + hub[0]);
            } else {
                p.edit().putInt(KEY_FAILURES, failures + 1).putLong(KEY_TRY_AT, now)
                        .putString(KEY_HUB_DETAIL, "the gateway answered HTTP " + status).apply();
                Log.w(TAG, "report HTTP " + status);
            }
        } catch (Exception e) {
            p.edit().putInt(KEY_FAILURES, failures + 1).putLong(KEY_TRY_AT, now)
                    .putString(KEY_HUB_DETAIL, "could not reach Jarvis: " + e.getClass().getSimpleName()).apply();
            Log.w(TAG, "report failed: " + e);
        }
        Listener l = listener;
        if (l != null) l.onChange(c);
    }

    /** {status, body}. */
    private static String[] put(String url, String token, JSONObject body) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        try {
            conn.setRequestMethod("PUT");
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("Authorization", "Bearer " + token);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
            int status = conn.getResponseCode();
            InputStream in = status >= 400 ? conn.getErrorStream() : conn.getInputStream();
            String text = "";
            if (in != null) {
                try (InputStream is = in) {
                    java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
                    byte[] chunk = new byte[4096];
                    int n;
                    while ((n = is.read(chunk)) > 0 && buf.size() < 64 * 1024) buf.write(chunk, 0, n);
                    text = buf.toString("UTF-8");
                }
            }
            return new String[] {Integer.toString(status), text};
        } finally {
            conn.disconnect();
        }
    }

    /** A state change asked for by the person (toggle, power): forget the last report so it goes now. */
    static void forceReport(Context c) {
        prefs(c).edit().remove(KEY_SENT_KEY).putInt(KEY_FAILURES, 0).apply();
    }
}
