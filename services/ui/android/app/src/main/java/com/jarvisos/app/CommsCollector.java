package com.jarvisos.app;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.provider.CallLog;
import android.provider.ContactsContract;
import android.util.Log;

import com.jarvisos.app.widgets.WidgetApi;

import org.json.JSONObject;

/**
 * Calls and text messages to the activity feed, from native code so they are
 * collected while the app is closed ({@link CommsSyncWorker}) as well as while
 * it is open (the WebView asks through {@code Comms.syncNow}).
 *
 * The WebView used to read them on a timer while Jarvis was open, newest
 * first and at most 200 at a time, and moved its cursor before anything was
 * uploaded: an upload that failed lost those calls for good, and on a busy
 * day the oldest rows past the 200 were skipped for good. Here rows are read
 * oldest first, each is posted, and the cursor (time, then row id, so two
 * rows in the same millisecond are not skipped) moves past a row only once
 * the server took it. A failure stops the run; the next one starts there.
 */
final class CommsCollector {
    private static final String TAG = "CommsCollector";
    static final String PREFS = "jarvis_comms";
    // The time cursors are the ones CommsPlugin used, so an update carries on
    // where the WebView collector left off.
    static final String KEY_CALLS = "last_calls_ts";
    static final String KEY_SMS = "last_sms_ts";
    static final String KEY_CALLS_ID = "last_calls_id";
    static final String KEY_SMS_ID = "last_sms_id";
    /** The most rows one run sends per kind; the rest follow on the next run. */
    static final int RUN_LIMIT = 200;
    /** How far back the very first run looks. */
    static final long FIRST_RUN_WINDOW_MS = 7L * 24 * 60 * 60 * 1000;

    private CommsCollector() {}

    static boolean hasPerm(Context context, String perm) {
        return context.checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED;
    }

    /** One pass over new calls and texts. Returns how many of each were sent. */
    static synchronized JSONObject run(Context context) {
        Context app = context.getApplicationContext();
        JSONObject out = new JSONObject();
        int calls = 0;
        int sms = 0;
        boolean ok = true;
        WidgetApi.ensureCredentials(app);
        String key = WidgetApi.apiKey(app);
        if (key != null && !key.isEmpty()) {
            try {
                if (hasPerm(app, Manifest.permission.READ_CALL_LOG)) calls = sendCalls(app);
                if (hasPerm(app, Manifest.permission.READ_SMS)) sms = sendSms(app);
            } catch (Exception e) {
                // Offline or a server error: the cursor stays on the first
                // row not taken, and the next run starts there.
                ok = false;
                Log.w(TAG, "comms upload stopped: " + e.getMessage());
            }
        }
        try {
            out.put("ok", ok);
            out.put("calls", calls);
            out.put("sms", sms);
        } catch (Exception ignored) {
            // A JSONObject with plain keys does not throw.
        }
        return out;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static int sendCalls(Context app) throws Exception {
        SharedPreferences prefs = prefs(app);
        long lastAt = startFrom(prefs.getLong(KEY_CALLS, 0L));
        long lastId = prefs.getLong(KEY_CALLS_ID, 0L);
        String[] projection = {
            CallLog.Calls._ID, CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME,
            CallLog.Calls.TYPE, CallLog.Calls.DATE, CallLog.Calls.DURATION,
        };
        int sent = 0;
        try (Cursor c = app.getContentResolver().query(
                CallLog.Calls.CONTENT_URI, projection, after(CallLog.Calls.DATE, CallLog.Calls._ID),
                afterArgs(lastAt, lastId), CallLog.Calls.DATE + " ASC, " + CallLog.Calls._ID + " ASC")) {
            while (c != null && c.moveToNext() && sent < RUN_LIMIT) {
                long id = c.getLong(c.getColumnIndexOrThrow(CallLog.Calls._ID));
                long at = c.getLong(c.getColumnIndexOrThrow(CallLog.Calls.DATE));
                String number = orEmpty(c.getString(c.getColumnIndexOrThrow(CallLog.Calls.NUMBER)));
                String name = orEmpty(c.getString(c.getColumnIndexOrThrow(CallLog.Calls.CACHED_NAME)));
                if (name.isEmpty()) name = contactName(app, number);
                String direction = callDirection(c.getInt(c.getColumnIndexOrThrow(CallLog.Calls.TYPE)));
                JSONObject detail = new JSONObject()
                    .put("number", number).put("direction", direction).put("at", at).put("source", "phone");
                JSONObject body = new JSONObject()
                    .put("feature", "calls").put("event", "call")
                    .put("item", name.isEmpty() ? number : name)
                    .put("duration_s", c.getLong(c.getColumnIndexOrThrow(CallLog.Calls.DURATION)))
                    .put("detail", detail);
                postOrSkip(app, body);
                prefs.edit().putLong(KEY_CALLS, at).putLong(KEY_CALLS_ID, id).apply();
                sent++;
            }
        }
        return sent;
    }

    private static int sendSms(Context app) throws Exception {
        SharedPreferences prefs = prefs(app);
        long lastAt = startFrom(prefs.getLong(KEY_SMS, 0L));
        long lastId = prefs.getLong(KEY_SMS_ID, 0L);
        String[] projection = {"_id", "address", "body", "date", "type"};
        int sent = 0;
        try (Cursor c = app.getContentResolver().query(
                Uri.parse("content://sms"), projection, after("date", "_id"),
                afterArgs(lastAt, lastId), "date ASC, _id ASC")) {
            while (c != null && c.moveToNext() && sent < RUN_LIMIT) {
                long id = c.getLong(c.getColumnIndexOrThrow("_id"));
                long at = c.getLong(c.getColumnIndexOrThrow("date"));
                String number = orEmpty(c.getString(c.getColumnIndexOrThrow("address")));
                String name = contactName(app, number);
                String direction = smsDirection(c.getInt(c.getColumnIndexOrThrow("type")));
                JSONObject detail = new JSONObject()
                    .put("number", number)
                    .put("body", orEmpty(c.getString(c.getColumnIndexOrThrow("body"))))
                    .put("direction", direction).put("at", at).put("source", "phone");
                JSONObject body = new JSONObject()
                    .put("feature", "sms").put("event", "message")
                    .put("item", name.isEmpty() ? number : name)
                    .put("detail", detail);
                postOrSkip(app, body);
                prefs.edit().putLong(KEY_SMS, at).putLong(KEY_SMS_ID, id).apply();
                sent++;
            }
        }
        return sent;
    }

    /**
     * Post one row. A row the server refuses outright (a 4xx other than
     * 401/403/404) is logged and passed over, so one odd row cannot pin the
     * cursor forever; anything else (offline, a 5xx, signed out) throws and
     * the run stops on that row.
     */
    private static void postOrSkip(Context app, JSONObject body) throws Exception {
        try {
            WidgetApi.post(app, "/api/activity", body);
        } catch (IllegalStateException e) {
            String msg = e.getMessage() == null ? "" : e.getMessage();
            if (msg.startsWith("HTTP 4") && !msg.startsWith("HTTP 401") && !msg.startsWith("HTTP 403")
                    && !msg.startsWith("HTTP 404")) {
                Log.w(TAG, "activity refused, skipped: " + msg);
                return;
            }
            throw e;
        }
    }

    /** Rows after the cursor: a later time, or the same time and a later id. */
    static String after(String dateColumn, String idColumn) {
        return dateColumn + " > ? OR (" + dateColumn + " = ? AND " + idColumn + " > ?)";
    }

    static String[] afterArgs(long at, long id) {
        String a = String.valueOf(at);
        return new String[] {a, a, String.valueOf(id)};
    }

    /** Where the first run starts: a week back, not the whole history. */
    static long startFrom(long cursor) {
        return cursor == 0L ? System.currentTimeMillis() - FIRST_RUN_WINDOW_MS : cursor;
    }

    private static String orEmpty(String s) {
        return s == null ? "" : s;
    }

    static String callDirection(int type) {
        switch (type) {
            case CallLog.Calls.INCOMING_TYPE: return "in";
            case CallLog.Calls.OUTGOING_TYPE: return "out";
            case CallLog.Calls.MISSED_TYPE: return "missed";
            case CallLog.Calls.REJECTED_TYPE: return "rejected";
            case CallLog.Calls.VOICEMAIL_TYPE: return "voicemail";
            case CallLog.Calls.BLOCKED_TYPE: return "blocked";
            default: return "unknown";
        }
    }

    static String smsDirection(int type) {
        switch (type) {
            case 1: return "in";     // inbox
            case 2: return "out";    // sent
            case 3: return "draft";
            case 4: return "outbox";
            case 5: return "failed";
            case 6: return "queued";
            default: return "unknown";
        }
    }

    private static String contactName(Context context, String number) {
        if (number.isEmpty() || !hasPerm(context, Manifest.permission.READ_CONTACTS)) return "";
        try (Cursor c = context.getContentResolver().query(
                Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number)),
                new String[] {ContactsContract.PhoneLookup.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) return orEmpty(c.getString(0));
        } catch (Exception e) {
            // Best effort only: the number is shown instead.
        }
        return "";
    }
}
