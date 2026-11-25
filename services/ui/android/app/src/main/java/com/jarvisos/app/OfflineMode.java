package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Log;

import com.jarvisos.app.widgets.WidgetApi;

import org.json.JSONArray;
import org.json.JSONObject;

import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.spec.ECGenParameterSpec;
import java.util.ArrayList;
import java.util.List;
import java.util.TimeZone;

/**
 * Offline mode on the phone (see {@link OfflineRules} for the rules): Jarvis
 * requests queued for a family phone that is online, the household's cached
 * information, a parent's commands reaching a child's phone, and the
 * Bluetooth-off log. All of it travels in {@link FamilyMesh}'s store over the
 * same short Bluetooth LE syncs as the offline chat; nothing here opens a
 * connection or asks for a permission of its own.
 */
final class OfflineMode {
    private static final String TAG = "OfflineMode";
    static final String PREFS = "jarvis_offline_mode";
    private static final String KEY_GUARD = "child_guard";
    private static final String KEY_OVERLAY = "overlay";
    private static final String KEY_CHILD_KEYS = "child_keys";
    private static final String KEY_SEQ = "parent_seq";
    private static final String KEY_KEYS_AT = "keys_at";
    private static final String KEY_REGISTERED_AT = "registered_at";
    private static final String KEY_SNAPSHOT_AT = "snapshot_at";
    private static final String KEY_OFF_LOG = "off_log";
    private static final String KEY_LAST_RELAY = "last_relay";
    private static final String EC_ALIAS = "jarvis_offline_ec";
    private static final long KEYS_REFRESH_MS = 6L * 3600_000L;
    private static final long SNAPSHOT_REFRESH_MS = 15 * 60_000L;
    private static final long COMMAND_LIFE_S = 6 * 3600;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Object LOCK = new Object();

    private OfflineMode() {}

    static SharedPreferences prefs(Context c) {
        return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static String me(Context c) {
        String u = TokenBridgePlugin.prefs(c).getString(TokenBridgePlugin.KEY_USERNAME, "");
        return u == null ? "" : u.split("\\.")[u.split("\\.").length - 1].toLowerCase(java.util.Locale.ROOT);
    }

    static String apiKey(Context c) {
        WidgetApi.ensureCredentials(c);
        return WidgetApi.apiKey(c);
    }

    // -- the device key (EC P-256 in the Android Keystore) ----------------------------

    static PrivateKey deviceKey() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (!ks.containsAlias(EC_ALIAS)) {
            KeyPairGenerator g = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
            g.initialize(new KeyGenParameterSpec.Builder(EC_ALIAS, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .build());
            g.generateKeyPair();
        }
        return (PrivateKey) ks.getKey(EC_ALIAS, null);
    }

    static String devicePublicKey() throws Exception {
        deviceKey();
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        return java.util.Base64.getEncoder().encodeToString(ks.getCertificate(EC_ALIAS).getPublicKey().getEncoded());
    }

    // -- online upkeep: register the key, refresh trusted keys and the snapshot ---------

    /** While online (FamilyMesh's server sync): keep keys and the cache fresh. Blocks; not the main thread. */
    static void onlineUpkeep(Context c) {
        long now = System.currentTimeMillis();
        SharedPreferences p = prefs(c);
        try {
            if (now - p.getLong(KEY_REGISTERED_AT, 0L) > 24 * 3600_000L) {
                WidgetApi.post(c, "/api/offline-keys", new JSONObject()
                        .put("device_id", PushGateway.deviceId(c)).put("public_key", devicePublicKey()));
                p.edit().putLong(KEY_REGISTERED_AT, now).apply();
            }
        } catch (Exception e) {
            Log.i(TAG, "key not registered: " + e.getMessage());
        }
        try {
            if (now - p.getLong(KEY_KEYS_AT, 0L) > KEYS_REFRESH_MS) {
                JSONObject keys = WidgetApi.get(c, "/api/offline-keys");
                synchronized (LOCK) {
                    OfflineRules.ChildGuard g = guard(c);
                    g.setKeys(keys);
                    p.edit().putString(KEY_GUARD, g.toJson().toString())
                            .putString(KEY_CHILD_KEYS, String.valueOf(keys.optJSONArray("children")))
                            .putLong(KEY_KEYS_AT, now).apply();
                }
            }
        } catch (Exception e) {
            Log.i(TAG, "keys not refreshed: " + e.getMessage());
        }
        try {
            if (now - p.getLong(KEY_SNAPSHOT_AT, 0L) > SNAPSHOT_REFRESH_MS) {
                JSONObject snap = WidgetApi.get(c, "/api/offline/snapshot");
                JSONArray items = snap.optJSONArray("items");
                List<MeshRules.Item> cached = new ArrayList<>();
                for (int i = 0; items != null && i < items.length(); i++) {
                    JSONObject it = items.optJSONObject(i);
                    if (it == null) continue;
                    MeshRules.Item m = new MeshRules.Item(OfflineRules.cacheId(it.optString("to"), it.optString("key")),
                            now / 1000L + MeshRules.CACHE_TTL_S, 0, it.optJSONObject("env"));
                    m.type = MeshRules.T_CACHE;
                    m.version = it.optLong("version");
                    m.meta = new JSONObject().put("to", it.optString("to")).put("key", it.optString("key"));
                    m.receivedAtMs = now;
                    cached.add(m);
                }
                FamilyMesh.addAll(c, cached);
                p.edit().putLong(KEY_SNAPSHOT_AT, now).apply();
            }
        } catch (Exception e) {
            Log.i(TAG, "snapshot not refreshed: " + e.getMessage());
        }
    }

    // -- Jarvis requests ----------------------------------------------------------------

    /** Queue a question, a quick command, a location share or a shopping edit. Returns its id. */
    static String queue(Context context, String kind, JSONObject body) throws Exception {
        Context c = context.getApplicationContext();
        String key = apiKey(c);
        String me = me(c);
        if (key == null || key.isEmpty() || me.isEmpty()) throw new IllegalArgumentException("Sign in to Jarvis first");
        long nowS = System.currentTimeMillis() / 1000L;
        JSONObject r = OfflineRules.request(key, MeshRules.newId(RANDOM), me, kind, body, nowS, RANDOM);
        MeshRules.Item item = new MeshRules.Item(r.getString("id"), r.getLong("expires_at"), 0, r.getJSONObject("env"));
        item.type = MeshRules.T_REQUEST;
        JSONObject meta = new JSONObject(r.toString());
        meta.remove("env");
        item.meta = meta;
        item.mine = true;
        item.receivedAtMs = System.currentTimeMillis();
        FamilyMesh.addAll(c, java.util.Collections.singletonList(item));
        FamilyMesh.trySyncWithServer(c, true);
        return item.id;
    }

    /** The full request (meta plus envelope) as the gateway takes it. */
    static JSONObject wireRequest(MeshRules.Item i) throws Exception {
        return new JSONObject(i.meta.toString()).put("env", i.env);
    }

    /** This phone's own requests and their answers, opened, for the app. */
    static JSONArray myRequests(Context context, boolean anyPeerRecently, boolean online) throws Exception {
        Context c = context.getApplicationContext();
        JSONArray out = new JSONArray();
        String key = apiKey(c);
        if (key == null || key.isEmpty()) return out;
        byte[] seal = OfflineRules.deriveKeys(key)[1];
        String me = me(c);
        MeshRules.Store s = FamilyMesh.store(c);
        long nowS = System.currentTimeMillis() / 1000L;
        for (MeshRules.Item i : s.items.values()) {
            if (!MeshRules.T_REQUEST.equals(i.type) || i.meta == null || !me.equals(i.meta.optString("from"))) continue;
            JSONObject body = MeshRules.open(seal, i.env);
            JSONObject answer = null;
            MeshRules.Item a = s.items.get(answerId(i.id));
            if (a != null) answer = MeshRules.open(seal, a.env);
            JSONObject row = new JSONObject().put("id", i.id).put("kind", i.meta.optString("kind"))
                    .put("body", body == null ? JSONObject.NULL : body)
                    .put("issued_at", i.meta.optLong("issued_at")).put("expires_at", i.meta.optLong("expires_at"))
                    .put("status", OfflineRules.requestStatus(answer != null, i.expiresS <= nowS, anyPeerRecently, online));
            if (answer != null) row.put("answer", answer.optString("text")).put("ok", answer.optBoolean("ok"));
            out.put(row);
        }
        return out;
    }

    /** The mesh id of a request's answer (one per request). */
    static String answerId(String requestId) {
        return OfflineRules.cacheId("answer", requestId);
    }

    /** A /api/offline-relay/submit result, kept as an answer item the sender's phone opens. */
    static MeshRules.Item answerItem(String requestId, JSONObject result, long nowMs) throws Exception {
        JSONObject env = result.optJSONObject("answer");
        if (env == null) return null;
        MeshRules.Item a = new MeshRules.Item(answerId(requestId), nowMs / 1000L + MeshRules.TTL_S, 0, env);
        a.type = MeshRules.T_ANSWER;
        a.meta = new JSONObject().put("to", result.optString("to")).put("req", requestId);
        a.receivedAtMs = nowMs;
        return a;
    }

    /** This phone's cached household information, opened (only its own member's items open). */
    static JSONArray cache(Context context) throws Exception {
        Context c = context.getApplicationContext();
        JSONArray out = new JSONArray();
        String key = apiKey(c);
        if (key == null || key.isEmpty()) return out;
        byte[] seal = OfflineRules.deriveKeys(key)[1];
        String me = me(c);
        for (MeshRules.Item i : FamilyMesh.store(c).items.values()) {
            if (!i.isCache() || i.meta == null || !me.equals(i.meta.optString("to"))) continue;
            JSONObject inner = MeshRules.open(seal, i.env);
            if (inner == null) continue;
            byte[] packed = java.util.Base64.getDecoder().decode(inner.optString("data"));
            String json;
            try (java.util.zip.GZIPInputStream in = new java.util.zip.GZIPInputStream(new java.io.ByteArrayInputStream(packed))) {
                java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
                byte[] chunk = new byte[4096];
                int n;
                while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
                json = buf.toString("UTF-8");
            }
            out.put(new JSONObject().put("key", inner.optString("key")).put("version", inner.optLong("version"))
                    .put("data", new org.json.JSONTokener(json).nextValue()));
        }
        return out;
    }

    // -- parent commands ----------------------------------------------------------------

    static OfflineRules.ChildGuard guard(Context c) {
        try {
            return OfflineRules.ChildGuard.fromJson(new JSONObject(prefs(c).getString(KEY_GUARD, "{}")));
        } catch (Exception e) {
            return new OfflineRules.ChildGuard();
        }
    }

    static OfflineRules.Overlay overlay(Context c) {
        try {
            return OfflineRules.Overlay.fromJson(new JSONObject(prefs(c).getString(KEY_OVERLAY, "{}")));
        } catch (Exception e) {
            return new OfflineRules.Overlay();
        }
    }

    /** A parent sends a command to a child's phone over Bluetooth. Returns its id. */
    static String sendCommand(Context context, String child, String action, int minutes) throws Exception {
        Context c = context.getApplicationContext();
        String me = me(c);
        long seq;
        synchronized (LOCK) {
            // Never repeats or goes back, even across restarts (and never below the clock, so a reinstall still climbs).
            seq = Math.max(prefs(c).getLong(KEY_SEQ, 0L) + 1, System.currentTimeMillis() / 1000L);
            prefs(c).edit().putLong(KEY_SEQ, seq).apply();
        }
        long nowS = System.currentTimeMillis() / 1000L;
        JSONObject cmd = OfflineRules.command(deviceKey(), MeshRules.newId(RANDOM), me, PushGateway.deviceId(c),
                child, action, minutes, nowS, COMMAND_LIFE_S, seq);
        ProximityRules.Config cfg = Proximity.config(c);
        if (cfg == null || cfg.chatKey == null) throw new IllegalArgumentException("Open Jarvis online once so it can get the family key");
        MeshRules.Item item = new MeshRules.Item(cmd.getString("id"), nowS + COMMAND_LIFE_S, 0,
                MeshRules.seal(cfg.chatKey, new JSONObject().put("command", cmd), RANDOM));
        item.type = MeshRules.T_COMMAND;
        item.meta = new JSONObject().put("to", child).put("from", me);
        item.mine = true;
        item.receivedAtMs = System.currentTimeMillis();
        FamilyMesh.addAll(c, java.util.Collections.singletonList(item));
        return item.id;
    }

    /** After a sync: apply any parent command for this (child's) phone and answer it. */
    static void onMeshChanged(Context context) {
        Context c = context.getApplicationContext();
        if (!GuardApi.isProtected(c)) return;
        ProximityRules.Config cfg = Proximity.config(c);
        if (cfg == null || cfg.chatKey == null) return;
        String me = me(c);
        long nowMs = System.currentTimeMillis();
        List<MeshRules.Item> acks = new ArrayList<>();
        synchronized (LOCK) {
            OfflineRules.ChildGuard g = guard(c);
            OfflineRules.Overlay o = overlay(c);
            List<JSONObject> fresh = new ArrayList<>();
            for (MeshRules.Item i : FamilyMesh.store(c).items.values()) {
                if (!MeshRules.T_COMMAND.equals(i.type) || i.meta == null || !me.equals(i.meta.optString("to"))) continue;
                JSONObject inner = MeshRules.open(cfg.chatKey, i.env);
                JSONObject cmd = inner == null ? null : inner.optJSONObject("command");
                if (cmd == null || g.seen.contains(cmd.optString("id"))) continue;
                fresh.add(cmd);
            }
            // The most recent decision wins: in signed-time, then sequence, order.
            fresh.sort((a, b) -> a.optLong("issued_at") != b.optLong("issued_at")
                    ? Long.compare(a.optLong("issued_at"), b.optLong("issued_at")) : Long.compare(a.optLong("seq"), b.optLong("seq")));
            for (JSONObject cmd : fresh) {
                OfflineRules.Verdict v = g.check(cmd, me, nowMs / 1000L);
                if (v == OfflineRules.Verdict.ALREADY_SEEN) continue;
                String result = v == OfflineRules.Verdict.OK ? "applied" : "refused_" + v.name().toLowerCase(java.util.Locale.ROOT);
                if (v == OfflineRules.Verdict.OK) {
                    g.accept(cmd);
                    o.apply(cmd, nowMs);
                    if ("bonus".equals(cmd.optString("action"))) {
                        long until = Math.max(GuardApi.prefs(c).getLong(GuardApi.KEY_UNLOCKED_UNTIL, 0L), nowMs)
                                + cmd.optLong("arg") * 60_000L;
                        GuardApi.prefs(c).edit().putLong(GuardApi.KEY_UNLOCKED_UNTIL, until).apply();
                    } else if ("lock".equals(cmd.optString("action"))) {
                        GuardApi.clearUnlock(c);
                    }
                    GuardApi.reportTamper(c, "offline_parent_command",
                            cmd.optString("action") + " from " + cmd.optString("parent"));
                } else {
                    g.seen.add(cmd.optString("id"));
                }
                try {
                    JSONObject ack = OfflineRules.ack(deviceKey(), cmd.optString("id"), me, PushGateway.deviceId(c),
                            nowMs / 1000L, result);
                    MeshRules.Item a = new MeshRules.Item(ackId(cmd.optString("id")), nowMs / 1000L + MeshRules.TTL_S, 0,
                            MeshRules.seal(cfg.chatKey, new JSONObject().put("ack", ack), RANDOM));
                    a.type = MeshRules.T_ACK;
                    a.meta = new JSONObject().put("to", cmd.optString("parent")).put("cmd", cmd.optString("id"));
                    a.receivedAtMs = nowMs;
                    acks.add(a);
                } catch (Exception e) {
                    Log.w(TAG, "acknowledgement not made: " + e.getMessage());
                }
            }
            try {
                prefs(c).edit().putString(KEY_GUARD, g.toJson().toString()).putString(KEY_OVERLAY, o.toJson().toString()).apply();
            } catch (Exception e) {
                Log.w(TAG, "guard state not saved: " + e.getMessage());
            }
        }
        if (!acks.isEmpty()) {
            FamilyMesh.addAll(c, acks);
            GuardService.onFreshLock(c);
        }
    }

    static String ackId(String commandId) {
        return OfflineRules.cacheId("ack", commandId);
    }

    /** Whether the phone is locked by a parent's command applied offline, newer than the server's last word. */
    static Boolean overlayLock(Context c, long statusAtMs, String serverReason) {
        OfflineRules.Overlay o = overlay(c);
        if (o.lockSetAtS == 0 || o.lockSetAtS * 1000L <= statusAtMs) return null;
        long now = System.currentTimeMillis();
        if (o.locked(now)) return Boolean.TRUE;
        // A newer offline unlock lifts the server's pause (never bedtime or a limit).
        return "paused".equals(serverReason) ? Boolean.FALSE : null;
    }

    /** A parent's commands and what became of them, for the parent's app. */
    static JSONArray myCommands(Context context) throws Exception {
        Context c = context.getApplicationContext();
        JSONArray out = new JSONArray();
        ProximityRules.Config cfg = Proximity.config(c);
        if (cfg == null || cfg.chatKey == null) return out;
        String me = me(c);
        MeshRules.Store s = FamilyMesh.store(c);
        JSONArray childKeys;
        try {
            childKeys = new JSONArray(prefs(c).getString(KEY_CHILD_KEYS, "[]"));
        } catch (Exception e) {
            childKeys = new JSONArray();
        }
        for (MeshRules.Item i : s.items.values()) {
            if (!MeshRules.T_COMMAND.equals(i.type) || i.meta == null || !me.equals(i.meta.optString("from"))) continue;
            JSONObject inner = MeshRules.open(cfg.chatKey, i.env);
            JSONObject cmd = inner == null ? null : inner.optJSONObject("command");
            if (cmd == null) continue;
            JSONObject row = new JSONObject().put("id", cmd.optString("id")).put("child", cmd.optString("child"))
                    .put("action", cmd.optString("action")).put("arg", cmd.optLong("arg"))
                    .put("issued_at", cmd.optLong("issued_at")).put("reported", i.synced);
            MeshRules.Item a = s.items.get(ackId(cmd.optString("id")));
            JSONObject ack = a == null ? null : MeshRules.open(cfg.chatKey, a.env);
            ack = ack == null ? null : ack.optJSONObject("ack");
            if (ack != null && verifiedAck(childKeys, ack)) {
                row.put("confirmed_at", ack.optLong("applied_at")).put("result", ack.optString("result"));
            }
            out.put(row);
        }
        return out;
    }

    private static boolean verifiedAck(JSONArray childKeys, JSONObject ack) {
        for (int k = 0; k < childKeys.length(); k++) {
            JSONObject key = childKeys.optJSONObject(k);
            if (key != null && key.optString("user").equals(ack.optString("child"))
                    && key.optString("device").equals(ack.optString("device"))
                    && OfflineRules.ecVerify(key.optString("pub"), OfflineRules.ackBytes(ack), ack.optString("sig"))) {
                return true;
            }
        }
        return false;
    }

    /** Hand carried parent commands (with acknowledgements) to the server; returns the ids it took. */
    static List<String> reportCommands(Context c, MeshRules.Store s) throws Exception {
        ProximityRules.Config cfg = Proximity.config(c);
        List<String> done = new ArrayList<>();
        if (cfg == null || cfg.chatKey == null) return done;
        JSONArray batch = new JSONArray();
        List<String> ids = new ArrayList<>();
        for (MeshRules.Item i : s.items.values()) {
            if (!MeshRules.T_COMMAND.equals(i.type) || i.synced) continue;
            JSONObject inner = MeshRules.open(cfg.chatKey, i.env);
            JSONObject cmd = inner == null ? null : inner.optJSONObject("command");
            if (cmd == null) continue;
            JSONObject entry = new JSONObject().put("command", cmd);
            MeshRules.Item a = s.items.get(ackId(cmd.optString("id")));
            JSONObject ack = a == null ? null : MeshRules.open(cfg.chatKey, a.env);
            if (ack != null && ack.optJSONObject("ack") != null) entry.put("ack", ack.optJSONObject("ack"));
            batch.put(entry);
            ids.add(i.id);
        }
        if (batch.length() == 0) return done;
        JSONObject answer = WidgetApi.post(c, "/api/offline-guard/report", new JSONObject().put("commands", batch));
        JSONObject results = answer.optJSONObject("results");
        for (String id : ids) {
            JSONObject r = results == null ? null : results.optJSONObject(id);
            if (r == null) continue;
            String status = r.optString("status");
            // Not yet applied (the parent's key could not be used, or another phone is handing it in): keep it.
            if ("in_progress".equals(status) || "not_applied".equals(status)) continue;
            // Applied, or refused for good. A child's acknowledgement the server has not recorded keeps it travelling.
            boolean ackPending = s.items.get(ackId(id)) != null && r.isNull("ack");
            if (!ackPending) done.add(id);
        }
        return done;
    }

    // -- Bluetooth / airplane mode off (a child's phone) --------------------------------

    static void radioChanged(Context context, String what, boolean off) {
        Context c = context.getApplicationContext();
        if (!GuardApi.isProtected(c)) return;
        long now = System.currentTimeMillis();
        String key = KEY_OFF_LOG + "_" + what;
        long since = prefs(c).getLong(key, 0L);
        if (off) {
            if (since == 0L) prefs(c).edit().putLong(key, now).apply();
            return;
        }
        if (since > 0L && now > since) {
            // Guardian's tamper reports are kept on the phone and sent when it is back online:
            // "<child>'s phone had Bluetooth off from 3:10 PM to 3:42 PM."
            GuardApi.reportTamper(c, "airplane".equals(what) ? "airplane_mode" : "bluetooth_off",
                    OfflineRules.OffLog.span(since, now, TimeZone.getDefault()));
        }
        prefs(c).edit().remove(key).apply();
    }

    static JSONObject diagnostics(Context context) throws Exception {
        Context c = context.getApplicationContext();
        MeshRules.Store s = FamilyMesh.store(c);
        int queued = 0;
        int carrying = 0;
        int cache = 0;
        int commands = 0;
        for (MeshRules.Item i : s.items.values()) {
            if (MeshRules.T_REQUEST.equals(i.type) && !i.synced) {
                if (i.mine) queued++;
                else carrying++;
            }
            if (i.isCache()) cache++;
            if (MeshRules.T_COMMAND.equals(i.type) && !i.synced) commands++;
        }
        SharedPreferences p = prefs(c);
        OfflineRules.ChildGuard g = guard(c);
        return new JSONObject().put("queued", queued).put("carrying", carrying).put("cacheItems", cache)
                .put("commandsWaiting", commands)
                .put("snapshotAt", p.getLong(KEY_SNAPSHOT_AT, 0L) > 0 ? p.getLong(KEY_SNAPSHOT_AT, 0L) : JSONObject.NULL)
                .put("lastRelayAt", p.getLong(KEY_LAST_RELAY, 0L) > 0 ? p.getLong(KEY_LAST_RELAY, 0L) : JSONObject.NULL)
                .put("parentKeys", g.parentKeys.size()).put("keysVersion", g.keysVersion)
                .put("protectedPhone", GuardApi.isProtected(c));
    }

    static void noteRelayed(Context c) {
        prefs(c).edit().putLong(KEY_LAST_RELAY, System.currentTimeMillis()).apply();
    }
}
