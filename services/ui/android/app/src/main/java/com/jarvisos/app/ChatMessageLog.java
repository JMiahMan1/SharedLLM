package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.util.Log;

import com.jarvisos.app.widgets.WidgetApi;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Received chat messages (RCS: Samsung Messages' and Google Messages' "chat")
 * that never reach the SMS store, from their notifications.
 *
 * Plain texts and MMS are read from Android's message store
 * ({@link CommsCollector}); a chat message is kept by the messaging app alone,
 * and no other app can read it. Its notification is the only trace, so each
 * one is held here, and once {@link #SETTLE_MS} has passed it is posted as a
 * received text marked {@code rcs} unless the same message turned up in the
 * SMS or MMS store by then (an ordinary text also notifies; it is already
 * logged). A chat message sent from the phone posts no notification and
 * cannot be logged.
 */
final class ChatMessageLog {
    private static final String TAG = "ChatMessageLog";
    static final String PREFS = "jarvis_chat_messages";
    static final String KEY = "pending";
    /** Messaging apps whose notifications may carry a chat message. */
    static final java.util.Set<String> MESSAGING_APPS = new java.util.HashSet<>(java.util.Arrays.asList(
            "com.samsung.android.messaging",
            "com.google.android.apps.messaging"));
    /** How long a text has to reach the SMS store before its notification is taken as chat. */
    static final long SETTLE_MS = 3 * 60 * 1000L;
    /** How far apart a notification and its stored text may be timed. */
    static final long MATCH_WINDOW_MS = 10 * 60 * 1000L;
    static final int MAX_PENDING = 300;

    private ChatMessageLog() {}

    /** One notified message: who, what, and when the notification came. */
    static final class Pending {
        final String sender;
        final String body;
        final long at;

        Pending(String sender, String body, long at) {
            this.sender = sender;
            this.body = body;
            this.at = at;
        }
    }

    /** A text from the SMS/MMS store, for matching: its words and its time. */
    static final class Stored {
        final String body;
        final long at;

        Stored(String body, long at) {
            this.body = body;
            this.at = at;
        }
    }

    /** Words compared as a person reads them: spacing and case set aside. */
    static String norm(String s) {
        return s == null ? "" : s.replaceAll("\\s+", " ").trim().toLowerCase(java.util.Locale.ROOT);
    }

    /**
     * Whether a notification shows this stored text. A long message is cut
     * short in its notification, sometimes with an ellipsis, so its start is
     * enough.
     */
    static boolean sameMessage(String notified, String stored) {
        String a = norm(notified).replaceAll("(…|\\.\\.\\.)$", "").trim();
        String b = norm(stored);
        if (a.isEmpty() || b.isEmpty()) return false;
        return a.equals(b) || b.startsWith(a) || a.startsWith(b);
    }

    /**
     * Splits the held notifications at ``nowMs``: those to post as chat (old
     * enough, and no stored text matched them), and those to keep waiting.
     * Matched ones are dropped (the text is already logged).
     */
    static List<Pending> settle(List<Pending> pending, List<Stored> stored, long nowMs, List<Pending> keep) {
        List<Pending> post = new ArrayList<>();
        for (Pending p : pending) {
            boolean matched = false;
            for (Stored s : stored) {
                if (Math.abs(s.at - p.at) <= MATCH_WINDOW_MS && sameMessage(p.body, s.body)) {
                    matched = true;
                    break;
                }
            }
            if (matched) continue;
            if (nowMs - p.at >= SETTLE_MS) post.add(p);
            else keep.add(p);
        }
        return post;
    }

    // Storage and Android plumbing below.

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static synchronized void add(Context context, String sender, String body, long atMs) {
        if (body == null || body.trim().isEmpty()) return;
        List<Pending> all = load(context);
        for (Pending p : all) {
            // The same message posted twice (an update to the notification).
            if (p.sender.equals(sender) && norm(p.body).equals(norm(body)) && Math.abs(p.at - atMs) < 60_000L) return;
        }
        all.add(new Pending(sender == null ? "" : sender, body, atMs));
        while (all.size() > MAX_PENDING) all.remove(0);
        save(context, all);
    }

    /**
     * Posts the chat messages that settled. Called from the comms run, after
     * the SMS and MMS have been read; throws (keeping the rest) when a post
     * fails, like the other comms posts.
     */
    static synchronized int flush(Context context) throws Exception {
        List<Pending> all = load(context);
        if (all.isEmpty()) return 0;
        long earliest = Long.MAX_VALUE;
        for (Pending p : all) earliest = Math.min(earliest, p.at);
        List<Stored> stored = recentInbox(context, earliest - MATCH_WINDOW_MS);
        List<Pending> keep = new ArrayList<>();
        List<Pending> post = settle(all, stored, System.currentTimeMillis(), keep);
        int sent = 0;
        try {
            for (Pending p : post) {
                JSONObject detail = new JSONObject()
                    .put("number", "").put("body", p.body).put("direction", "in")
                    .put("at", p.at).put("source", "phone").put("rcs", true);
                JSONObject body = new JSONObject()
                    .put("feature", "sms").put("event", "message")
                    .put("item", p.sender).put("detail", detail);
                WidgetApi.post(context.getApplicationContext(), "/api/activity", body);
                sent++;
            }
        } finally {
            // Whatever was not sent waits for the next run, oldest first.
            List<Pending> left = new ArrayList<>(post.subList(sent, post.size()));
            left.addAll(keep);
            save(context, left);
        }
        return sent;
    }

    /** Received texts from the SMS and MMS stores since ``sinceMs``. */
    private static List<Stored> recentInbox(Context context, long sinceMs) {
        List<Stored> out = new ArrayList<>();
        try (Cursor c = context.getContentResolver().query(Uri.parse("content://sms/inbox"),
                new String[] {"body", "date"}, "date >= ?", new String[] {String.valueOf(sinceMs)}, null)) {
            while (c != null && c.moveToNext()) out.add(new Stored(c.getString(0), c.getLong(1)));
        } catch (Exception e) {
            Log.w(TAG, "sms unreadable: " + e.getMessage());
        }
        try (Cursor c = context.getContentResolver().query(Uri.parse("content://mms/inbox"),
                new String[] {"_id", "date"}, "date >= ?", new String[] {String.valueOf(sinceMs / 1000L)}, null)) {
            while (c != null && c.moveToNext()) {
                long id = c.getLong(0);
                long at = c.getLong(1) * 1000L;
                try (Cursor parts = context.getContentResolver().query(Uri.parse("content://mms/part"),
                        new String[] {"text"}, "mid = ? AND ct = 'text/plain'", new String[] {String.valueOf(id)}, null)) {
                    while (parts != null && parts.moveToNext()) out.add(new Stored(parts.getString(0), at));
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "mms unreadable: " + e.getMessage());
        }
        return out;
    }

    private static List<Pending> load(Context context) {
        List<Pending> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(prefs(context).getString(KEY, "[]"));
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                out.add(new Pending(o.optString("sender", ""), o.optString("body", ""), o.optLong("at", 0)));
            }
        } catch (Exception e) {
            // A store that does not parse starts again empty.
        }
        return out;
    }

    private static void save(Context context, List<Pending> all) {
        JSONArray a = new JSONArray();
        try {
            for (Pending p : all) a.put(new JSONObject().put("sender", p.sender).put("body", p.body).put("at", p.at));
        } catch (Exception ignored) {
            // Plain strings and numbers do not throw.
        }
        prefs(context).edit().putString(KEY, a.toString()).apply();
    }
}
