package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Location fixes the server could not take when they were made, kept on the
 * phone until it can.
 *
 * {@link LocationTrackingService} used to post each fix once and drop it when
 * the post failed, so an hour without signal (or a server restart) left a hole
 * in the history and a drive through it never became a trip. Here a fix that
 * could not be sent is written to disk with its own fix time, and the queue is
 * sent oldest first: before the next live fix, and on every background sync
 * run ({@link CommsSyncWorker}).
 *
 * Oldest first, and ahead of the live fix, because the server takes the last
 * fix it is sent as the person's current position, and Geo ignores a fix
 * older than the newest it has seen when it builds trips and arrivals. A
 * backlog sent after the live fix would put the family map back in the past
 * and never become a trip.
 *
 * The queue rules (cap, dedupe, flush order) work on a {@link Store} and a
 * {@link Sender}, so they can be tested without Android.
 */
final class LocationOutbox {
    private static final String TAG = "LocationOutbox";
    private static final String PREFS = "jarvis_location_outbox";
    private static final String KEY_PENDING = "pending";
    /** The most fixes kept (about 16 hours of driving at one fix per 30 s); the oldest go first. */
    static final int MAX_PENDING = 2000;
    /** The most fixes one flush sends, so a long backlog never holds a run up for long. */
    static final int FLUSH_LIMIT = 200;
    private static final int CONNECT_TIMEOUT_MS = 10_000;
    private static final int READ_TIMEOUT_MS = 15_000;

    /** What became of one fix sent to the server. */
    enum Sent {
        /** The server took it. */
        OK,
        /** Not now (offline, a timeout, a 5xx): keep it and try later. */
        RETRY,
        /** The server will never take it (a 404 or 422): drop it. */
        REFUSED,
        /** The key was refused (401/403): not this fix's fault, so keep it, but stop asking. */
        SIGNED_OUT
    }

    /** Where a flush stopped. */
    enum Flushed {
        /** Nothing is waiting. */
        EMPTY,
        /** Sent {@link #FLUSH_LIMIT} and more are waiting. */
        MORE,
        /** A fix could not be sent now; it and the rest are kept. */
        RETRY,
        /** The key was refused; everything is kept for when it is accepted again. */
        SIGNED_OUT,
        /** Another flush is running. */
        BUSY
    }

    /** The pending fixes as stored: each is {"user": ..., "body": {...}}. */
    interface Store {
        JSONArray load();

        void save(JSONArray pending);
    }

    interface Sender {
        Sent send(JSONObject body);
    }

    private static final Object LOCK = new Object();
    private static final AtomicBoolean FLUSHING = new AtomicBoolean(false);

    private LocationOutbox() {}

    /**
     * What an HTTP status means for a fix. 408 and 429 are "try again" as much
     * as a 5xx is; any other 4xx is a refusal the same fix would get again.
     */
    static Sent classify(int status) {
        if (status >= 200 && status < 300) return Sent.OK;
        if (status == 401 || status == 403) return Sent.SIGNED_OUT;
        if (status >= 500 || status == 408 || status == 429) return Sent.RETRY;
        return Sent.REFUSED;
    }

    /**
     * Keep one fix for {@code user}. The same fix (same time and place) is
     * kept once: the service re-sends a cached fix whenever it restarts, and
     * a phone offline all night would otherwise fill the queue with it.
     */
    static void add(Store store, String user, JSONObject body) {
        String id = fixId(user, body);
        synchronized (LOCK) {
            JSONArray pending = store.load();
            for (int i = 0; i < pending.length(); i++) {
                JSONObject item = pending.optJSONObject(i);
                if (item != null && id.equals(fixId(item.optString("user"), item.optJSONObject("body")))) {
                    return;
                }
            }
            while (pending.length() >= MAX_PENDING) pending.remove(0);
            JSONObject item = new JSONObject();
            try {
                item.put("user", user);
                item.put("body", body);
            } catch (Exception e) {
                return;
            }
            pending.put(item);
            store.save(pending);
        }
    }

    /** Whether any fix is waiting. */
    static boolean hasPending(Store store) {
        synchronized (LOCK) {
            return store.load().length() > 0;
        }
    }

    /**
     * Send up to {@code limit} waiting fixes for {@code user}, oldest first,
     * and stop at the first that cannot be sent now so the rest keep their
     * order. A fix leaves the queue only once the server took it (or refused
     * it for good). Fixes kept for someone else, from before another person
     * signed in on this phone, are dropped: posting them would put that
     * person's trips in this one's history.
     */
    static Flushed flush(Store store, String user, Sender sender, int limit) {
        if (!FLUSHING.compareAndSet(false, true)) return Flushed.BUSY;
        List<String> done = new ArrayList<>();
        Flushed result = Flushed.EMPTY;
        try {
            JSONArray pending;
            synchronized (LOCK) {
                pending = store.load();
            }
            int sent = 0;
            for (int i = 0; i < pending.length(); i++) {
                JSONObject item = pending.optJSONObject(i);
                if (item == null) continue;
                JSONObject body = item.optJSONObject("body");
                if (body == null || !item.optString("user").equals(user)) {
                    done.add(item.toString());
                    continue;
                }
                if (sent >= limit) {
                    result = Flushed.MORE;
                    break;
                }
                Sent outcome = sender.send(body);
                if (outcome == Sent.RETRY) {
                    result = Flushed.RETRY;
                    break;
                }
                if (outcome == Sent.SIGNED_OUT) {
                    result = Flushed.SIGNED_OUT;
                    break;
                }
                // Taken, or refused for good (send() logged it): either way it leaves.
                done.add(item.toString());
                sent++;
            }
        } finally {
            if (!done.isEmpty()) {
                synchronized (LOCK) {
                    // By content, not position: fixes may have been added (and
                    // the oldest trimmed) while this ran.
                    JSONArray now = store.load();
                    JSONArray left = new JSONArray();
                    for (int i = 0; i < now.length(); i++) {
                        Object item = now.opt(i);
                        if (item == null || !done.remove(item.toString())) left.put(item);
                    }
                    store.save(left);
                }
            }
            FLUSHING.set(false);
        }
        return result;
    }

    /**
     * Send a new fix: whatever is waiting first, then this one. It is kept
     * when it cannot go now (offline, a 5xx), and dropped when it was refused,
     * since the same fix would be refused again. Returns what became of it.
     */
    static Sent sendLive(Store store, String user, JSONObject body, Sender sender) {
        if (hasPending(store)) {
            Flushed backlog = flush(store, user, sender, FLUSH_LIMIT);
            if (backlog == Flushed.RETRY || backlog == Flushed.BUSY) {
                // Still unreachable, or the background run is sending the
                // backlog right now: this fix waits behind it, in order.
                add(store, user, body);
                return Sent.RETRY;
            }
            // The key was refused for the backlog; this one would be too.
            if (backlog == Flushed.SIGNED_OUT) return Sent.SIGNED_OUT;
            // MORE: a backlog longer than one flush. The live fix goes now
            // regardless; the rest follows on later runs, as late fixes.
        }
        Sent sent = sender.send(body);
        if (sent == Sent.RETRY) add(store, user, body);
        return sent;
    }

    /** Same person, same fix time, same place: the same fix. */
    private static String fixId(String user, JSONObject body) {
        if (body == null) return user + "|";
        return user + "|" + body.optDouble("timestamp") + "|" + body.optDouble("latitude")
                + "|" + body.optDouble("longitude");
    }

    // Android: the queue in SharedPreferences, sent with TokenBridge's credentials.

    static Store store(Context context) {
        final SharedPreferences p = context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return new Store() {
            @Override
            public JSONArray load() {
                try {
                    return new JSONArray(p.getString(KEY_PENDING, "[]"));
                } catch (Exception e) {
                    return new JSONArray();
                }
            }

            @Override
            public void save(JSONArray pending) {
                p.edit().putString(KEY_PENDING, pending.toString()).apply();
            }
        };
    }

    /**
     * Send what is waiting for the signed-in person (the background sync run).
     * Returns false only when something is still waiting because it could not
     * be sent now; a refused key waits for the next sign-in instead of
     * retrying.
     */
    static boolean flush(Context context) {
        Store store = store(context);
        if (!hasPending(store)) return true;
        SharedPreferences p = TokenBridgePlugin.prefs(context);
        String user = p.getString(TokenBridgePlugin.KEY_USERNAME, null);
        String token = p.getString(TokenBridgePlugin.KEY_API_KEY, null);
        String server = p.getString(TokenBridgePlugin.KEY_SERVER_URL, null);
        if (isBlank(user) || isBlank(token) || isBlank(server)) return true;
        Flushed result = flush(store, user, body -> send(server, token, user, body), FLUSH_LIMIT);
        if (result != Flushed.EMPTY) Log.i(TAG, "flush: " + result);
        return result != Flushed.RETRY;
    }

    /** Post one fix to the person's location route, or the older collection route. */
    static Sent send(String serverUrl, String token, String user, JSONObject body) {
        String base = serverUrl.endsWith("/") ? serverUrl.substring(0, serverUrl.length() - 1) : serverUrl;
        try {
            int status = post(base + "/api/users/" + android.net.Uri.encode(user) + "/location", token, body);
            if (status == 404) {
                // Older gateways expose only the collection route.
                status = post(base + "/api/users/location", token, body);
            }
            if (status < 200 || status >= 300) Log.w(TAG, "upload HTTP " + status);
            return classify(status);
        } catch (Exception e) {
            // No network, a timeout, a dropped connection.
            Log.w(TAG, "upload failed: " + e);
            return Sent.RETRY;
        }
    }

    private static int post(String url, String token, JSONObject body) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        try {
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("Authorization", "Bearer " + token);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
            return conn.getResponseCode();
        } finally {
            conn.disconnect();
        }
    }

    private static boolean isBlank(String s) {
        return s == null || s.isEmpty();
    }
}
