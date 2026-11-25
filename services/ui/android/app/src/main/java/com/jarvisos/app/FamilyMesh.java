package com.jarvisos.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattServer;
import android.bluetooth.BluetoothGattServerCallback;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Build;
import android.os.Handler;
import android.util.Log;

import com.jarvisos.app.widgets.WidgetApi;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.ByteBuffer;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * The offline family chat over Bluetooth LE (see {@link MeshRules} for the
 * messages, the store and the sync, and the PR's design notes).
 *
 * Rides on {@link Proximity}: it hears the family's phones there, and the
 * advertisement says whether this phone has messages to pass on. Only then is a
 * short GATT connection made (no pairing, so no prompt on either phone): one
 * sync, then disconnect. A phone that cannot advertise (some tablets) has no
 * GATT server; it connects out to the family's phones instead.
 *
 * Every phone's own messages go to the family chat when it is online again
 * (POST /api/family-mesh/sync, posted once by message id), and copies carried
 * for others are marked done when the family chat has them (/synced).
 */
final class FamilyMesh {
    private static final String TAG = "FamilyMesh";
    static final String PREFS = "jarvis_family_mesh";
    private static final String KEY_ENABLED = "enabled";
    private static final String KEY_STORE = "store";
    private static final String KEY_ROOM = "room";
    private static final String KEY_LAST_SENT = "last_sent";
    private static final String KEY_LAST_RECEIVED = "last_received";
    private static final String KEY_LAST_SYNC = "last_sync";
    private static final String KEY_LAST_SERVER = "last_server";
    private static final long SESSION_TIMEOUT_MS = 20_000L;
    private static final long SERVER_SYNC_MS = 60_000L;

    interface Listener {
        void onChange();
    }

    private static final Object LOCK = new Object();
    private static final SecureRandom RANDOM = new SecureRandom();
    private static volatile Listener listener;
    private static BluetoothGattServer server;
    private static long serverPeriod = Long.MIN_VALUE;
    private static final Map<String, MeshRules.Session> serverSessions = new HashMap<>();
    private static final Map<String, byte[]> serverPending = new HashMap<>();
    private static BluetoothGatt client;
    private static String clientPeer;
    /** Peer -> {when we last synced, the store's digest hash then}. */
    private static final Map<String, long[]> lastWith = new HashMap<>();
    private static long lastServerTry;
    private static String lastError;

    private FamilyMesh() {}

    static void setListener(Listener l) {
        listener = l;
    }

    static SharedPreferences prefs(Context c) {
        return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean enabled(Context c) {
        return prefs(c).getBoolean(KEY_ENABLED, false);
    }

    static void setEnabled(Context c, boolean on) {
        prefs(c).edit().putBoolean(KEY_ENABLED, on).apply();
    }

    static void setRoom(Context c, String room) {
        prefs(c).edit().putString(KEY_ROOM, room == null ? "" : room).apply();
    }

    static String room(Context c) {
        return prefs(c).getString(KEY_ROOM, "");
    }

    /** Connecting (GATT) needs Nearby devices' connect on Android 12+; earlier, nothing more. */
    static boolean connectPermission(Context c) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true;
        return c.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED;
    }

    static boolean active(Context c) {
        return enabled(c) && Proximity.enabled(c) && connectPermission(c);
    }

    static MeshRules.Store store(Context c) {
        synchronized (LOCK) {
            return MeshRules.Store.decode(prefs(c).getString(KEY_STORE, "[]"));
        }
    }

    private static void save(Context c, MeshRules.Store s) {
        try {
            prefs(c).edit().putString(KEY_STORE, s.encode()).apply();
        } catch (Exception e) {
            Log.w(TAG, "store not saved: " + e.getMessage());
        }
    }

    private static void changed() {
        Listener l = listener;
        if (l != null) l.onChange();
    }

    /** Whether this phone has messages a peer might lack (the advertisement's flag). */
    static boolean hasMail(Context c) {
        return MeshRules.hasMail(store(c), System.currentTimeMillis() / 1000L);
    }

    /** The advertisement's tag of what this phone holds (changes whenever its messages do). */
    static int mailTagBits(Context c) {
        return ProximityRules.mailTagBits(MeshRules.digestHash(store(c), System.currentTimeMillis() / 1000L));
    }

    // -- writing ------------------------------------------------------------------------

    /** Seal and keep a new message; returns its id. IllegalArgumentException says why not. */
    static String send(Context context, String kind, JSONObject fields) throws Exception {
        Context c = context.getApplicationContext();
        ProximityRules.Config cfg = Proximity.config(c);
        if (cfg == null || cfg.chatKey == null) throw new IllegalArgumentException("Open Jarvis online once so it can get the family key");
        String room = room(c);
        if (room.isEmpty()) throw new IllegalArgumentException("Choose your family chat in Chat first; offline messages go there");
        long nowMs = System.currentTimeMillis();
        JSONObject payload = MeshRules.payload(kind, cfg.me, room, fields, nowMs / 1000L);
        MeshRules.Item item = new MeshRules.Item(MeshRules.newId(RANDOM), nowMs / 1000L + MeshRules.TTL_S, 0,
                MeshRules.seal(cfg.chatKey, payload, RANDOM));
        item.mine = true;
        item.receivedAtMs = nowMs;
        synchronized (LOCK) {
            MeshRules.Store s = store(c);
            s.add(item, nowMs / 1000L);
            save(c, s);
        }
        prefs(c).edit().putLong(KEY_LAST_SENT, nowMs).apply();
        Proximity.mailChanged();
        changed();
        trySyncWithServer(c, true);
        return item.id;
    }

    /** Every message this phone holds, opened, for the chat sheet (oldest first). */
    static JSONArray list(Context context) {
        Context c = context.getApplicationContext();
        JSONArray out = new JSONArray();
        ProximityRules.Config cfg = Proximity.config(c);
        if (cfg == null || cfg.chatKey == null) return out;
        List<MeshRules.Item> items = new ArrayList<>(store(c).items.values());
        items.sort((a, b) -> Long.compare(a.receivedAtMs, b.receivedAtMs));
        for (MeshRules.Item i : items) {
            JSONObject p = MeshRules.open(cfg.chatKey, i.env);
            if (p == null) continue;
            try {
                p.put("id", i.id).put("mine", i.mine).put("synced", i.synced).put("hops", i.hops);
                out.put(p);
            } catch (Exception ignored) {
                // Plain values: org.json does not throw here.
            }
        }
        return out;
    }

    // -- the GATT server (phones that can advertise) ------------------------------------

    static UUID[] gattIds(byte[] key, long period) {
        return new UUID[] {
            uuid(key, "jarvis-mesh-gatt-v1|" + period),
            uuid(key, "jarvis-mesh-in-v1|" + period),
            uuid(key, "jarvis-mesh-out-v1|" + period),
        };
    }

    private static UUID uuid(byte[] key, String label) {
        byte[] h = ProximityRules.hmac(key, label);
        h[6] = (byte) ((h[6] & 0x0f) | 0x40);
        h[8] = (byte) ((h[8] & 0x3f) | 0x80);
        ByteBuffer b = ByteBuffer.wrap(h, 0, 16);
        return new UUID(b.getLong(), b.getLong());
    }

    /** Open (or move to this period's ids) the GATT server; Proximity calls it while advertising. */
    @SuppressLint("MissingPermission")
    static void ensureServer(Context context, ProximityRules.Config cfg, long period) {
        Context c = context.getApplicationContext();
        if (!active(c) || cfg == null) {
            closeServer();
            return;
        }
        synchronized (LOCK) {
            if (server != null && serverPeriod == period) return;
            closeServer();
            BluetoothManager bm = (BluetoothManager) c.getSystemService(Context.BLUETOOTH_SERVICE);
            if (bm == null) return;
            try {
                server = bm.openGattServer(c, serverCallback(c, cfg, period));
            } catch (SecurityException e) {
                server = null;
            }
            if (server == null) {
                lastError = "GATT server unavailable";
                return;
            }
            UUID[] ids = gattIds(cfg.key, period);
            BluetoothGattService svc = new BluetoothGattService(ids[0], BluetoothGattService.SERVICE_TYPE_PRIMARY);
            svc.addCharacteristic(new BluetoothGattCharacteristic(ids[1],
                    BluetoothGattCharacteristic.PROPERTY_WRITE, BluetoothGattCharacteristic.PERMISSION_WRITE));
            svc.addCharacteristic(new BluetoothGattCharacteristic(ids[2],
                    BluetoothGattCharacteristic.PROPERTY_READ, BluetoothGattCharacteristic.PERMISSION_READ));
            server.addService(svc);
            serverPeriod = period;
        }
    }

    @SuppressLint("MissingPermission")
    static void closeServer() {
        synchronized (LOCK) {
            if (server != null) {
                try {
                    server.close();
                } catch (RuntimeException ignored) {
                    // Already gone with Bluetooth.
                }
            }
            server = null;
            serverPeriod = Long.MIN_VALUE;
            serverSessions.clear();
            serverPending.clear();
        }
    }

    private static BluetoothGattServerCallback serverCallback(Context c, ProximityRules.Config cfg, long period) {
        return new BluetoothGattServerCallback() {
            @Override
            public void onConnectionStateChange(BluetoothDevice device, int status, int newState) {
                String a = device.getAddress();
                if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                    MeshRules.Session s;
                    synchronized (LOCK) {
                        s = serverSessions.remove(a);
                        serverPending.remove(a);
                    }
                    if (s != null) finishSession(c, s, null);
                }
            }

            @SuppressLint("MissingPermission")
            @Override
            public void onCharacteristicWriteRequest(BluetoothDevice device, int requestId, BluetoothGattCharacteristic ch,
                                                     boolean preparedWrite, boolean responseNeeded, int offset, byte[] value) {
                boolean ok;
                synchronized (LOCK) {
                    MeshRules.Session s = serverSessions.get(device.getAddress());
                    if (s == null) {
                        // The HELLO proves the household key the advertisement was made with.
                        s = new MeshRules.Session(store(c), cfg.key, period, 182, System.currentTimeMillis());
                        serverSessions.put(device.getAddress(), s);
                    }
                    try {
                        ok = s.serverWrite(value);
                    } catch (Exception e) {
                        ok = false;
                    }
                }
                BluetoothGattServer srv = server;
                if (srv == null) return;
                try {
                    if (responseNeeded) {
                        srv.sendResponse(device, requestId, ok ? BluetoothGatt.GATT_SUCCESS : BluetoothGatt.GATT_FAILURE, offset, null);
                    }
                    if (!ok) srv.cancelConnection(device);
                } catch (SecurityException ignored) {
                    // Permission taken back mid-sync: the client times out.
                }
            }

            @SuppressLint("MissingPermission")
            @Override
            public void onCharacteristicReadRequest(BluetoothDevice device, int requestId, int offset,
                                                    BluetoothGattCharacteristic ch) {
                byte[] piece;
                synchronized (LOCK) {
                    String a = device.getAddress();
                    MeshRules.Session s = serverSessions.get(a);
                    piece = serverPending.get(a);
                    if (offset == 0 || piece == null) {
                        byte[] next = s == null || !s.authorised ? null : s.serverNextRead();
                        piece = next == null ? new byte[0] : next;
                        serverPending.put(a, piece);
                    }
                }
                byte[] slice = offset >= piece.length ? new byte[0] : java.util.Arrays.copyOfRange(piece, offset, piece.length);
                BluetoothGattServer srv = server;
                try {
                    if (srv != null) srv.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, offset, slice);
                } catch (SecurityException ignored) {
                    // As above.
                }
            }
        };
    }

    // -- the GATT client (the phone that has something to pass on) ----------------------

    /** Proximity heard a family phone: sync with it if the rules say so. */
    static void onPeer(Context context, String member, int flags, BluetoothDevice device) {
        Context c = context.getApplicationContext();
        if (!active(c) || device == null) return;
        ProximityRules.Config cfg = Proximity.config(c);
        if (cfg == null || cfg.chatKey == null) return;
        long now = System.currentTimeMillis();
        MeshRules.Store s = store(c);
        long digest = MeshRules.digestHash(s, now / 1000L);
        boolean go;
        synchronized (LOCK) {
            if (client != null) return;
            long[] last = lastWith.get(member);
            int peerTag = ProximityRules.mailTag(flags);
            go = MeshRules.shouldConnect(
                    (flags & ProximityRules.FLAG_CAN_CONNECT) != 0,
                    (flags & ProximityRules.FLAG_HAS_MAIL) != 0,
                    MeshRules.hasMail(s, now / 1000L),
                    last == null ? Long.MAX_VALUE : now - last[0],
                    last == null || last[1] != digest,
                    last == null || last[2] != peerTag,
                    cfg.me.compareTo(member) < 0,
                    Proximity.canServe());
            if (go) {
                lastWith.put(member, new long[] {now, digest, peerTag});
                clientPeer = member;
            }
        }
        if (go) connect(c, cfg, device, member);
    }

    @SuppressLint("MissingPermission")
    private static void connect(Context c, ProximityRules.Config cfg, BluetoothDevice device, String member) {
        long period = ProximityRules.period(System.currentTimeMillis(), cfg.periodS);
        Handler h = Proximity.handler();
        final MeshRules.Session[] session = new MeshRules.Session[1];
        final List<byte[]> toWrite = new ArrayList<>();
        final BluetoothGattCharacteristic[] chars = new BluetoothGattCharacteristic[2];
        BluetoothGattCallback cb = new BluetoothGattCallback() {
            @Override
            public void onConnectionStateChange(BluetoothGatt gatt, int status, int newState) {
                if (newState == BluetoothProfile.STATE_CONNECTED) {
                    try {
                        gatt.requestMtu(185);
                    } catch (SecurityException e) {
                        end(gatt, "no permission");
                    }
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                    end(gatt, session[0] != null && session[0].done ? null : "disconnected early");
                }
            }

            @Override
            public void onMtuChanged(BluetoothGatt gatt, int mtu, int status) {
                int piece = Math.max(20, Math.min(182, mtu - 3));
                MeshRules.Store s = store(c);
                session[0] = new MeshRules.Session(s, cfg.key, period, piece, System.currentTimeMillis());
                try {
                    gatt.discoverServices();
                } catch (SecurityException e) {
                    end(gatt, "no permission");
                }
            }

            @Override
            public void onServicesDiscovered(BluetoothGatt gatt, int status) {
                for (long p = period - 1; p <= period + 1 && chars[0] == null; p++) {
                    UUID[] ids = gattIds(cfg.key, p);
                    BluetoothGattService svc = gatt.getService(ids[0]);
                    if (svc != null) {
                        chars[0] = svc.getCharacteristic(ids[1]);
                        chars[1] = svc.getCharacteristic(ids[2]);
                    }
                }
                if (chars[0] == null || chars[1] == null) {
                    end(gatt, "not a family phone");
                    return;
                }
                toWrite.addAll(session[0].clientOpening());
                writeNext(gatt);
            }

            private void writeNext(BluetoothGatt gatt) {
                if (toWrite.isEmpty()) {
                    if (session[0].done) {
                        end(gatt, null);
                    } else {
                        readNext(gatt);
                    }
                    return;
                }
                byte[] piece = toWrite.remove(0);
                try {
                    boolean started;
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                        started = gatt.writeCharacteristic(chars[0], piece, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)
                                == android.bluetooth.BluetoothStatusCodes.SUCCESS;
                    } else {
                        started = legacyWrite(gatt, chars[0], piece);
                    }
                    if (!started) end(gatt, "write refused");
                } catch (SecurityException e) {
                    end(gatt, "no permission");
                }
            }

            private void readNext(BluetoothGatt gatt) {
                try {
                    if (!gatt.readCharacteristic(chars[1])) end(gatt, "read refused");
                } catch (SecurityException e) {
                    end(gatt, "no permission");
                }
            }

            @Override
            public void onCharacteristicWrite(BluetoothGatt gatt, BluetoothGattCharacteristic ch, int status) {
                if (status != BluetoothGatt.GATT_SUCCESS) {
                    end(gatt, "refused by the other phone");
                    return;
                }
                writeNext(gatt);
            }

            @Override
            public void onCharacteristicRead(BluetoothGatt gatt, BluetoothGattCharacteristic ch, byte[] value, int status) {
                handleRead(gatt, value, status);
            }

            @Override
            @SuppressWarnings("deprecation")
            public void onCharacteristicRead(BluetoothGatt gatt, BluetoothGattCharacteristic ch, int status) {
                handleRead(gatt, ch.getValue(), status);
            }

            private void handleRead(BluetoothGatt gatt, byte[] value, int status) {
                if (status != BluetoothGatt.GATT_SUCCESS || value == null) {
                    end(gatt, "read failed");
                    return;
                }
                if (value.length == 0) {
                    // Nothing queued yet: ask again in a moment.
                    if (h != null) h.postDelayed(() -> readNext(gatt), 100L);
                    return;
                }
                try {
                    List<byte[]> back = session[0].clientRead(value);
                    if (!back.isEmpty() || session[0].done) {
                        toWrite.addAll(back);
                        writeNext(gatt);
                    } else {
                        readNext(gatt);
                    }
                } catch (Exception e) {
                    end(gatt, "bad answer");
                }
            }

            private void end(BluetoothGatt gatt, String error) {
                synchronized (LOCK) {
                    if (client != gatt) return;
                    client = null;
                    clientPeer = null;
                }
                try {
                    gatt.disconnect();
                    gatt.close();
                } catch (SecurityException ignored) {
                    // Closing anyway.
                }
                MeshRules.Session s = session[0];
                if (s != null) finishSession(c, s, member);
                if (error != null) {
                    lastError = member + ": " + error;
                    Log.i(TAG, "sync with " + member + " ended: " + error);
                }
            }
        };
        BluetoothGatt gatt;
        try {
            gatt = device.connectGatt(c, false, cb, BluetoothDevice.TRANSPORT_LE);
        } catch (SecurityException e) {
            gatt = null;
        }
        synchronized (LOCK) {
            client = gatt;
            if (gatt == null) clientPeer = null;
        }
        if (gatt != null && h != null) {
            final BluetoothGatt g = gatt;
            h.postDelayed(() -> {
                synchronized (LOCK) {
                    if (client != g) return;
                }
                lastError = member + ": timed out";
                try {
                    g.disconnect();
                    g.close();
                } catch (SecurityException ignored) {
                    // Closing anyway.
                }
                synchronized (LOCK) {
                    if (client == g) {
                        client = null;
                        clientPeer = null;
                    }
                }
            }, SESSION_TIMEOUT_MS);
        }
    }

    @SuppressWarnings("deprecation")
    @SuppressLint("MissingPermission")
    private static boolean legacyWrite(BluetoothGatt gatt, BluetoothGattCharacteristic ch, byte[] piece) {
        ch.setValue(piece);
        ch.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
        return gatt.writeCharacteristic(ch);
    }

    /** Keep what a sync brought in; note it for the diagnostics and the sheet. */
    private static void finishSession(Context c, MeshRules.Session s, String peer) {
        synchronized (LOCK) {
            // The session worked on a copy of the store: merge what it received into the current one.
            MeshRules.Store now = store(c);
            long nowS = System.currentTimeMillis() / 1000L;
            for (MeshRules.Item i : s.store.items.values()) {
                if (!now.items.containsKey(i.id)) now.add(i, nowS);
            }
            save(c, now);
        }
        long t = System.currentTimeMillis();
        SharedPreferences.Editor e = prefs(c).edit().putString(KEY_LAST_SYNC, t + "|" + (peer == null ? "" : peer)
                + "|" + s.sent + "|" + s.received);
        if (s.received > 0) e.putLong(KEY_LAST_RECEIVED, t);
        e.apply();
        if (s.received > 0) {
            Proximity.mailChanged();
            changed();
        }
    }

    // -- back online --------------------------------------------------------------------

    static boolean online(Context c) {
        ConnectivityManager cm = (ConnectivityManager) c.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return false;
        Network n = cm.getActiveNetwork();
        NetworkCapabilities caps = n == null ? null : cm.getNetworkCapabilities(n);
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
    }

    /** Post this phone's own messages and mark what the family chat already has (Proximity calls it every flush). */
    static void trySyncWithServer(Context context, boolean now) {
        Context c = context.getApplicationContext();
        long t = System.currentTimeMillis();
        if (!enabled(c) || (!now && t - lastServerTry < SERVER_SYNC_MS) || !online(c)) return;
        lastServerTry = t;
        new Thread(() -> {
            try {
                WidgetApi.ensureCredentials(c);
                String key = WidgetApi.apiKey(c);
                if (key == null || key.isEmpty()) return;
                MeshRules.Store s = store(c);
                List<String> done = new ArrayList<>();
                List<MeshRules.Item> mine = s.unsyncedMine();
                if (!mine.isEmpty()) {
                    JSONArray items = new JSONArray();
                    for (MeshRules.Item i : mine) items.put(i.wire());
                    JSONObject answer = WidgetApi.post(c, "/api/family-mesh/sync", new JSONObject().put("items", items));
                    done.addAll(MeshRules.postedIds(answer));
                }
                List<String> others = new ArrayList<>();
                for (MeshRules.Item i : s.items.values()) if (!i.synced && !i.mine) others.add(i.id);
                if (!others.isEmpty()) {
                    JSONObject answer = WidgetApi.post(c, "/api/family-mesh/synced",
                            new JSONObject().put("ids", new JSONArray(others)));
                    JSONArray synced = answer.optJSONArray("synced");
                    for (int k = 0; synced != null && k < synced.length(); k++) done.add(synced.optString(k));
                }
                if (!done.isEmpty()) {
                    synchronized (LOCK) {
                        MeshRules.Store fresh = store(c);
                        fresh.markSynced(done);
                        save(c, fresh);
                    }
                    changed();
                }
                prefs(c).edit().putLong(KEY_LAST_SERVER, System.currentTimeMillis()).apply();
            } catch (Exception e) {
                // Offline after all, or an older server: the next flush tries again.
                Log.i(TAG, "server sync: " + e.getMessage());
            }
        }, "family-mesh-sync").start();
    }

    // -- diagnostics ----------------------------------------------------------------------

    static JSONObject diagnostics(Context context) {
        Context c = context.getApplicationContext();
        JSONObject o = new JSONObject();
        try {
            MeshRules.Store s = store(c);
            o.put("enabled", enabled(c));
            o.put("connectPermission", connectPermission(c));
            o.put("room", !room(c).isEmpty());
            o.put("server", server != null);
            o.put("syncing", clientPeer == null ? JSONObject.NULL : clientPeer);
            o.put("stored", s.items.size());
            o.put("unsynced", s.unsyncedMine().size());
            o.put("hasMail", MeshRules.hasMail(s, System.currentTimeMillis() / 1000L));
            long sent = prefs(c).getLong(KEY_LAST_SENT, 0L);
            long received = prefs(c).getLong(KEY_LAST_RECEIVED, 0L);
            long server = prefs(c).getLong(KEY_LAST_SERVER, 0L);
            o.put("lastSentAt", sent > 0 ? sent : JSONObject.NULL);
            o.put("lastReceivedAt", received > 0 ? received : JSONObject.NULL);
            o.put("lastServerSyncAt", server > 0 ? server : JSONObject.NULL);
            String[] last = prefs(c).getString(KEY_LAST_SYNC, "").split("\\|", -1);
            if (last.length == 4) {
                o.put("lastPeerSync", new JSONObject().put("at", Long.parseLong(last[0])).put("peer", last[1])
                        .put("sent", Integer.parseInt(last[2])).put("received", Integer.parseInt(last[3])));
            }
            o.put("lastError", lastError == null ? JSONObject.NULL : lastError);
        } catch (Exception ignored) {
            // Plain values: org.json does not throw here.
        }
        return o;
    }
}
