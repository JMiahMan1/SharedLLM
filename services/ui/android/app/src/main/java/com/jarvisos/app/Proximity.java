package com.jarvisos.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.le.AdvertiseCallback;
import android.bluetooth.le.AdvertiseData;
import android.bluetooth.le.AdvertiseSettings;
import android.bluetooth.le.BluetoothLeAdvertiser;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanRecord;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.ParcelUuid;
import android.util.Log;

import com.jarvisos.app.widgets.WidgetApi;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Family phones near each other over Bluetooth LE, with no network (see
 * {@link ProximityRules} for the scheme, the bands and the battery budget).
 *
 * Runs inside Wander's location service when that runs, else in NearbyService (one quiet
 * notification), so it has no notification of its
 * own and lives exactly as long as location sharing does. Turned on per
 * device by the person (Wander's "Find family nearby"), which is also the
 * only moment the Nearby devices permission is asked for (Android 12+; before
 * that, scanning uses the location permission Wander already holds).
 *
 * Discreet: no device name, no pairing, no prompt on anyone's phone (the
 * advertisement is connectable only while the offline chat is on, for its
 * short syncs); the advertisement is a rotating service UUID and
 * eight bytes only the family can resolve. A device that cannot advertise (some
 * tablets) still scans, and says so in diagnostics and the health report.
 *
 * Sightings are kept on the phone and sent when it is online (POST
 * /api/geo/proximity); nothing is lost while offline (the newest
 * {@link #OUTBOX_MAX} are kept).
 */
final class Proximity {
    private static final String TAG = "Proximity";
    static final String PREFS = "jarvis_proximity";
    private static final String KEY_ENABLED = "enabled";
    private static final String KEY_CONFIG = "config";
    private static final String KEY_CONFIG_AT = "config_at";
    private static final String KEY_OUTBOX = "outbox";
    private static final String KEY_UPLOADED_AT = "uploaded_at";
    static final int OUTBOX_MAX = 500;
    private static final long CONFIG_REFRESH_MS = 12L * 60 * 60 * 1000;
    private static final long FLUSH_MS = 30_000L;
    private static final long UPLOAD_MS = 5 * 60_000L;

    private static final Object LOCK = new Object();
    private static HandlerThread thread;
    private static Handler handler;
    private static Context app;
    private static volatile boolean running;
    private static volatile boolean driving;
    private static ProximityRules.Config config;
    private static ProximityRules.Resolver resolver;
    private static final ProximityRules.Aggregator aggregator = new ProximityRules.Aggregator();
    private static AdvertiseCallback advertiseCallback;
    private static String advertising = ProximityRules.ADV_OFF;
    private static int advertiseError;
    private static ScanCallback scanCallback;
    private static boolean scanning;
    private static long lastResultAt;
    private static long lastScanStartAt;
    /** Person -> {band, rssi, at} of the latest reading, for diagnostics. */
    private static final Map<String, Object[]> peers = new LinkedHashMap<>();

    private Proximity() {}

    static SharedPreferences prefs(Context c) {
        return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean enabled(Context c) {
        return prefs(c).getBoolean(KEY_ENABLED, false);
    }

    static void setEnabled(Context c, boolean on) {
        prefs(c).edit().putBoolean(KEY_ENABLED, on).apply();
    }

    /** Scanning's permission: Nearby devices on Android 12+, location (which Wander holds) before. */
    static boolean scanPermission(Context c) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            return c.checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) == PackageManager.PERMISSION_GRANTED;
        }
        return c.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    static boolean advertisePermission(Context c) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            return c.checkSelfPermission(Manifest.permission.BLUETOOTH_ADVERTISE) == PackageManager.PERMISSION_GRANTED;
        }
        return true;
    }

    /** Whether this hardware can advertise at all (null adapter / no multi-advertising: scan only). */
    static String advertiseCapability(Context c) {
        BluetoothAdapter a = VehicleBluetooth.adapter(c);
        if (a == null) return ProximityRules.ADV_UNSUPPORTED;
        boolean multi;
        BluetoothLeAdvertiser adv;
        try {
            multi = a.isMultipleAdvertisementSupported();
            adv = a.isEnabled() ? a.getBluetoothLeAdvertiser() : null;
        } catch (SecurityException e) {
            return ProximityRules.ADV_OFF;
        }
        if (!a.isEnabled()) return multi ? ProximityRules.ADV_OFF : ProximityRules.ADV_UNSUPPORTED;
        return ProximityRules.advertiseCapability(true, multi, adv != null);
    }

    // -- lifecycle (from LocationTrackingService) ------------------------------------

    private static boolean receiverRegistered;

    static void start(Context context) {
        synchronized (LOCK) {
            app = context.getApplicationContext();
            if (thread == null) {
                thread = new HandlerThread("proximity");
                thread.start();
                handler = new Handler(thread.getLooper());
            }
            if (!receiverRegistered) {
                // Bluetooth switched off stops everything; switched on starts it again.
                android.content.BroadcastReceiver r = new android.content.BroadcastReceiver() {
                    @Override
                    public void onReceive(Context ctx, android.content.Intent intent) {
                        if (android.content.Intent.ACTION_AIRPLANE_MODE_CHANGED.equals(intent.getAction())) {
                            // A child's phone keeps when it was cut off, for the parents (OfflineMode).
                            OfflineMode.radioChanged(ctx, "airplane", intent.getBooleanExtra("state", false));
                            return;
                        }
                        int st = intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR);
                        if (st == BluetoothAdapter.STATE_ON || st == BluetoothAdapter.STATE_OFF) {
                            OfflineMode.radioChanged(ctx, "bluetooth", st == BluetoothAdapter.STATE_OFF);
                            Handler h = handler;
                            if (h != null) h.post(Proximity::apply);
                        }
                    }
                };
                android.content.IntentFilter f = new android.content.IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED);
                f.addAction(android.content.Intent.ACTION_AIRPLANE_MODE_CHANGED);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    app.registerReceiver(r, f, Context.RECEIVER_EXPORTED);
                } else {
                    app.registerReceiver(r, f);
                }
                receiverRegistered = true;
            }
        }
        handler.post(Proximity::apply);
    }

    /** The household config this phone holds (null before the first online start). */
    static ProximityRules.Config config(Context c) {
        if (config == null) {
            try {
                config = ProximityRules.parseConfig(new JSONObject(prefs(c).getString(KEY_CONFIG, "{}")));
            } catch (Exception e) {
                return null;
            }
        }
        return config;
    }

    /** Whether a family phone was heard within {@code ms}. */
    static boolean heardWithin(long ms) {
        return lastResultAt > 0 && System.currentTimeMillis() - lastResultAt <= ms;
    }

    static Handler handler() {
        return handler;
    }

    /** Whether this phone accepts offline chat connections now (advertising and serving). */
    static boolean canServe() {
        return running && ProximityRules.ADV_ON.equals(advertising) && app != null && FamilyMesh.active(app);
    }

    /** The offline chat's messages changed: say so in the advertisement. */
    static void mailChanged() {
        Handler h = handler;
        if (h != null && running) h.post(Proximity::restartAdvertising);
    }

    static void stop() {
        Handler h = handler;
        if (h != null) h.post(() -> {
            FamilyMesh.closeServer();
            running = false;
            stopScan();
            stopAdvertising();
            flush(true);
            h.removeCallbacksAndMessages(null);
        });
    }

    /** The trip state changed: scan harder while driving, gently otherwise. */
    static void setDriving(boolean on) {
        if (driving == on) return;
        driving = on;
        Handler h = handler;
        if (h != null && running) h.post(() -> {
            stopScan();
            restartAdvertising();
            scanCycle();
        });
    }

    /** Turned on or off in Settings > Nearby & offline, or the permission changed. */
    static void refresh(Context context) {
        start(context);
    }

    private static void apply() {
        Context c = app;
        if (c == null) return;
        // Any part of Nearby & offline (or a child's parent link) needs the radio.
        boolean want = NearbyParts.engine(c) && scanPermission(c) && VehicleBluetooth.bluetoothOn(c);
        if (!want) {
            if (running) {
                running = false;
                stopScan();
                stopAdvertising();
                flush(true);
            }
            return;
        }
        loadConfig(c);
        if (config == null) {
            refreshConfig(c);
            handler.postDelayed(Proximity::apply, 60_000L);
            return;
        }
        running = true;
        rebuildResolver();
        restartAdvertising();
        handler.removeCallbacks(SCAN_CYCLE);
        scanCycle();
        schedulePeriodChange();
        handler.removeCallbacks(FLUSH);
        handler.postDelayed(FLUSH, FLUSH_MS);
    }

    // -- config -----------------------------------------------------------------------

    private static void loadConfig(Context c) {
        if (config != null) return;
        try {
            config = ProximityRules.parseConfig(new JSONObject(prefs(c).getString(KEY_CONFIG, "{}")));
        } catch (Exception e) {
            config = null;
        }
        if (System.currentTimeMillis() - prefs(c).getLong(KEY_CONFIG_AT, 0L) > CONFIG_REFRESH_MS) refreshConfig(c);
    }

    private static void refreshConfig(Context c) {
        new Thread(() -> {
            try {
                WidgetApi.ensureCredentials(c);
                String key = WidgetApi.apiKey(c);
                if (key == null || key.isEmpty()) return;
                JSONObject answer = WidgetApi.get(c, "/api/geo/proximity/config");
                ProximityRules.Config parsed = ProximityRules.parseConfig(answer);
                if (parsed == null) return;
                prefs(c).edit().putString(KEY_CONFIG, answer.toString())
                        .putLong(KEY_CONFIG_AT, System.currentTimeMillis()).apply();
                Handler h = handler;
                if (h != null) h.post(() -> {
                    config = parsed;
                    if (running) {
                        rebuildResolver();
                        restartAdvertising();
                        stopScan();
                        scanCycle();
                    } else {
                        apply();
                    }
                });
            } catch (Exception e) {
                // Offline: the kept config keeps working; the next start asks again.
                Log.i(TAG, "config not refreshed: " + e.getMessage());
            }
        }, "proximity-config").start();
    }

    private static void rebuildResolver() {
        long p = ProximityRules.period(System.currentTimeMillis(), config.periodS);
        resolver = new ProximityRules.Resolver(config.key, config.members, config.me, p);
    }

    private static final Runnable PERIOD = () -> {
        if (!running || config == null) return;
        rebuildResolver();
        restartAdvertising();
        stopScan();
        scanCycle();
        schedulePeriodChange();
    };

    private static void schedulePeriodChange() {
        long now = System.currentTimeMillis();
        handler.removeCallbacks(PERIOD);
        handler.postDelayed(PERIOD, Math.max(1000L, ProximityRules.nextPeriodAtMs(now, config.periodS) - now + 500L));
    }

    // -- advertising ------------------------------------------------------------------

    @SuppressLint("MissingPermission")
    private static void restartAdvertising() {
        stopAdvertising();
        Context c = app;
        if (c == null || config == null || config.me.isEmpty()) return;
        String capability = advertiseCapability(c);
        boolean mesh = FamilyMesh.active(c);
        if (!mesh) FamilyMesh.closeServer();
        if (!ProximityRules.ADV_ON.equals(capability)) {
            advertising = capability;
            return;
        }
        if (!advertisePermission(c)) {
            advertising = ProximityRules.ADV_OFF;
            return;
        }
        BluetoothLeAdvertiser adv = VehicleBluetooth.adapter(c).getBluetoothLeAdvertiser();
        long p = ProximityRules.period(System.currentTimeMillis(), config.periodS);
        UUID uuid = ProximityRules.serviceUuid(config.key, p);
        int flags = (driving ? ProximityRules.FLAG_MOVING : 0)
                | (mesh ? ProximityRules.FLAG_CAN_CONNECT : 0)
                | (mesh && FamilyMesh.hasMail(c) ? ProximityRules.FLAG_HAS_MAIL | FamilyMesh.mailTagBits(c) : 0);
        byte[] data = ProximityRules.payload(ProximityRules.memberId(config.key, config.me, p),
                flags, ProximityRules.DEFAULT_TX_AT_1M);
        // Connectable only for the offline chat (a short GATT sync, no pairing, no prompt).
        if (mesh) FamilyMesh.ensureServer(c, config, p);
        AdvertiseSettings settings = new AdvertiseSettings.Builder()
                .setAdvertiseMode(driving ? AdvertiseSettings.ADVERTISE_MODE_BALANCED : AdvertiseSettings.ADVERTISE_MODE_LOW_POWER)
                .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_MEDIUM)
                .setConnectable(mesh)
                .setTimeout(0)
                .build();
        AdvertiseData ad = new AdvertiseData.Builder()
                .setIncludeDeviceName(false)
                .setIncludeTxPowerLevel(false)
                .addServiceData(new ParcelUuid(uuid), data)
                .build();
        AdvertiseCallback cb = new AdvertiseCallback() {
            @Override
            public void onStartSuccess(AdvertiseSettings s) {
                if (advertiseCallback == this) {
                    advertising = ProximityRules.ADV_ON;
                    advertiseError = 0;
                }
            }

            @Override
            public void onStartFailure(int errorCode) {
                if (advertiseCallback != this) return;
                advertising = errorCode == AdvertiseCallback.ADVERTISE_FAILED_FEATURE_UNSUPPORTED
                        ? ProximityRules.ADV_UNSUPPORTED : ProximityRules.ADV_FAILED;
                advertiseError = errorCode;
                advertiseCallback = null;
                Log.w(TAG, "advertising failed: " + errorCode);
            }
        };
        advertiseCallback = cb;
        try {
            adv.startAdvertising(settings, ad, cb);
        } catch (SecurityException | IllegalStateException e) {
            advertiseCallback = null;
            advertising = ProximityRules.ADV_FAILED;
            Log.w(TAG, "advertising refused: " + e.getMessage());
        }
    }

    @SuppressLint("MissingPermission")
    private static void stopAdvertising() {
        AdvertiseCallback cb = advertiseCallback;
        advertiseCallback = null;
        if (!ProximityRules.ADV_UNSUPPORTED.equals(advertising)) advertising = ProximityRules.ADV_OFF;
        if (cb == null || app == null) return;
        try {
            BluetoothLeAdvertiser adv = VehicleBluetooth.adapter(app).getBluetoothLeAdvertiser();
            if (adv != null) adv.stopAdvertising(cb);
        } catch (RuntimeException e) {
            Log.i(TAG, "stop advertising: " + e.getMessage());
        }
    }

    // -- scanning ---------------------------------------------------------------------

    private static final Runnable SCAN_CYCLE = Proximity::scanCycle;

    private static void scanCycle() {
        if (!running) return;
        ProximityRules.ScanPlan plan = ProximityRules.scanPlan(driving);
        if (!scanning) startScan(plan.balanced);
        handler.removeCallbacks(SCAN_CYCLE);
        if (plan.offMs > 0) {
            handler.postDelayed(() -> {
                stopScan();
                handler.postDelayed(SCAN_CYCLE, plan.offMs);
            }, plan.onMs);
        } else {
            // Continuous; restarted now and then because Android stops a scan left running for 30 min.
            handler.postDelayed(() -> {
                stopScan();
                scanCycle();
            }, plan.onMs);
        }
    }

    @SuppressLint("MissingPermission")
    private static void startScan(boolean balanced) {
        Context c = app;
        if (c == null || resolver == null) return;
        BluetoothAdapter a = VehicleBluetooth.adapter(c);
        BluetoothLeScanner scanner;
        try {
            scanner = a == null ? null : a.getBluetoothLeScanner();
        } catch (SecurityException e) {
            scanner = null;
        }
        if (scanner == null) return;
        List<ScanFilter> filters = new ArrayList<>();
        for (UUID u : resolver.uuids) {
            filters.add(new ScanFilter.Builder().setServiceData(new ParcelUuid(u), new byte[0]).build());
        }
        ScanSettings settings = new ScanSettings.Builder()
                .setScanMode(balanced ? ScanSettings.SCAN_MODE_BALANCED : ScanSettings.SCAN_MODE_LOW_POWER)
                .setCallbackType(ScanSettings.CALLBACK_TYPE_ALL_MATCHES)
                .setReportDelay(0)
                .build();
        ScanCallback cb = new ScanCallback() {
            @Override
            public void onScanResult(int callbackType, ScanResult result) {
                Handler h = handler;
                if (h != null) h.post(() -> heard(result));
            }

            @Override
            public void onScanFailed(int errorCode) {
                scanning = false;
                Log.w(TAG, "scan failed: " + errorCode);
            }
        };
        try {
            scanner.startScan(filters, settings, cb);
            scanCallback = cb;
            scanning = true;
            lastScanStartAt = System.currentTimeMillis();
        } catch (SecurityException | IllegalStateException e) {
            Log.w(TAG, "scan refused: " + e.getMessage());
        }
    }

    @SuppressLint("MissingPermission")
    private static void stopScan() {
        ScanCallback cb = scanCallback;
        scanCallback = null;
        scanning = false;
        if (cb == null || app == null) return;
        try {
            BluetoothAdapter a = VehicleBluetooth.adapter(app);
            BluetoothLeScanner scanner = a == null ? null : a.getBluetoothLeScanner();
            if (scanner != null) scanner.stopScan(cb);
        } catch (RuntimeException e) {
            Log.i(TAG, "stop scan: " + e.getMessage());
        }
    }

    private static void heard(ScanResult result) {
        ProximityRules.Resolver r = resolver;
        ScanRecord record = result.getScanRecord();
        if (r == null || record == null || record.getServiceData() == null) return;
        for (Map.Entry<ParcelUuid, byte[]> e : record.getServiceData().entrySet()) {
            ProximityRules.Heard h = r.resolve(e.getKey().getUuid(), e.getValue());
            if (h == null) continue;
            long now = System.currentTimeMillis();
            lastResultAt = now;
            // Who was near is recorded only while Find family nearby is on; the
            // chat and offline mode hear phones only to sync with them.
            if (NearbyParts.sightings(app)) aggregator.add(h.member, result.getRssi(), h.txAt1m, now);
            synchronized (peers) {
                peers.put(h.member, new Object[] {ProximityRules.band(result.getRssi(), h.txAt1m), result.getRssi(), now});
            }
            FamilyMesh.onPeer(app, h.member, h.flags, result.getDevice());
            return;
        }
    }

    // -- sightings out ----------------------------------------------------------------

    private static final Runnable FLUSH = () -> {
        flush(false);
        if (app != null) FamilyMesh.trySyncWithServer(app, false);
        if (running) handler.postDelayed(Proximity.FLUSH, FLUSH_MS);
    };

    private static void flush(boolean everything) {
        Context c = app;
        if (c == null) return;
        List<ProximityRules.Sighting> done = aggregator.close(System.currentTimeMillis(), everything);
        if (!done.isEmpty()) {
            synchronized (LOCK) {
                JSONArray box = outbox(c);
                for (ProximityRules.Sighting s : done) {
                    try {
                        box.put(s.toJson());
                    } catch (Exception ignored) {
                        // Plain values: org.json does not throw here.
                    }
                }
                while (box.length() > OUTBOX_MAX) box.remove(0);
                prefs(c).edit().putString(KEY_OUTBOX, box.toString()).apply();
            }
        }
        long last = prefs(c).getLong(KEY_UPLOADED_AT, 0L);
        if (outbox(c).length() > 0 && (everything || System.currentTimeMillis() - last >= UPLOAD_MS)) uploadAsync(c);
    }

    private static JSONArray outbox(Context c) {
        try {
            return new JSONArray(prefs(c).getString(KEY_OUTBOX, "[]"));
        } catch (Exception e) {
            return new JSONArray();
        }
    }

    private static void uploadAsync(Context c) {
        new Thread(() -> {
            JSONArray sending;
            synchronized (LOCK) {
                sending = outbox(c);
            }
            if (sending.length() == 0) return;
            try {
                WidgetApi.ensureCredentials(c);
                String key = WidgetApi.apiKey(c);
                if (key == null || key.isEmpty()) return;
                WidgetApi.post(c, "/api/geo/proximity", new JSONObject().put("events", sending));
                synchronized (LOCK) {
                    JSONArray now = outbox(c);
                    JSONArray rest = new JSONArray();
                    // Keep anything added while this upload was on its way.
                    for (int i = sending.length(); i < now.length(); i++) rest.put(now.opt(i));
                    prefs(c).edit().putString(KEY_OUTBOX, rest.toString())
                            .putLong(KEY_UPLOADED_AT, System.currentTimeMillis()).apply();
                }
            } catch (Exception e) {
                // Offline: kept for the next try.
                Log.i(TAG, "sightings kept for later: " + e.getMessage());
            }
        }, "proximity-upload").start();
    }

    // -- what Wander's diagnostics and the health report show -------------------------

    static JSONObject diagnostics(Context context) {
        Context c = context.getApplicationContext();
        JSONObject o = new JSONObject();
        try {
            o.put("enabled", enabled(c));
            o.put("scanPermission", scanPermission(c));
            o.put("advertisePermission", advertisePermission(c));
            o.put("bluetoothOn", VehicleBluetooth.bluetoothOn(c));
            o.put("running", running);
            String adv = running ? advertising : advertiseCapability(c);
            o.put("advertising", ProximityRules.ADV_ON.equals(adv) && !running ? ProximityRules.ADV_OFF : adv);
            if (advertiseError != 0) o.put("advertiseError", advertiseError);
            o.put("scanning", scanning);
            o.put("scanMode", driving ? "balanced" : "low_power");
            o.put("driving", driving);
            o.put("lastScanStartAt", lastScanStartAt > 0 ? lastScanStartAt : JSONObject.NULL);
            o.put("lastHeardAt", lastResultAt > 0 ? lastResultAt : JSONObject.NULL);
            ProximityRules.Config cfg = config;
            o.put("configured", cfg != null);
            o.put("keyId", cfg == null ? JSONObject.NULL : cfg.keyId);
            o.put("members", cfg == null ? 0 : cfg.members.size());
            o.put("outbox", outbox(c).length());
            long up = prefs(c).getLong(KEY_UPLOADED_AT, 0L);
            o.put("uploadedAt", up > 0 ? up : JSONObject.NULL);
            JSONArray list = new JSONArray();
            synchronized (peers) {
                for (Map.Entry<String, Object[]> e : peers.entrySet()) {
                    Object[] v = e.getValue();
                    list.put(new JSONObject().put("person", e.getKey())
                            .put("band", v[0] == null ? "too_far" : v[0]).put("rssi", v[1]).put("at", v[2]));
                }
            }
            o.put("peers", list);
        } catch (Exception ignored) {
            // Plain values: org.json does not throw here.
        }
        return o;
    }

    /** The health report's line: {ok, outcome, message}. */
    static Object[] health(Context context) {
        Context c = context.getApplicationContext();
        String adv = running ? advertising : advertiseCapability(c);
        String[] h = ProximityRules.healthOutcome(NearbyParts.engine(c), scanPermission(c), VehicleBluetooth.bluetoothOn(c), adv);
        return new Object[] {"ok".equals(h[0]), h[0], h[1]};
    }
}
