package com.jarvisos.app;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.provider.CallLog;
import android.provider.ContactsContract;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.util.ArrayList;
import java.util.List;

/**
 * Calls and SMS: reads the device's call log and SMS store so the family
 * profile can see who called or texted, when, and (for SMS) what was said.
 * Runtime permissions (READ_CALL_LOG, READ_SMS) are required; the person
 * grants them in the app's permission dialog. Contacts are resolved
 * best-effort for display names.
 */
@CapacitorPlugin(name = "Comms")
public class CommsPlugin extends Plugin {

    private static final String PREFS = "jarvis_comms";
    private static final String KEY_CALLS = "last_calls_ts";
    private static final String KEY_SMS = "last_sms_ts";
    private static final int LIMIT = 200;
    private static final long FIRST_RUN_WINDOW_MS = 7L * 24 * 60 * 60 * 1000;

    @PluginMethod
    public void hasAccess(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("calls", hasPermission(Manifest.permission.READ_CALL_LOG));
        ret.put("sms", hasPermission(Manifest.permission.READ_SMS));
        call.resolve(ret);
    }

    @PluginMethod
    public void requestAccess(PluginCall call) {
        List<String> needed = new ArrayList<>();
        if (!hasPermission(Manifest.permission.READ_CALL_LOG)) {
            needed.add(Manifest.permission.READ_CALL_LOG);
        }
        if (!hasPermission(Manifest.permission.READ_SMS)) {
            needed.add(Manifest.permission.READ_SMS);
        }
        if (!hasPermission(Manifest.permission.READ_CONTACTS)) {
            needed.add(Manifest.permission.READ_CONTACTS);
        }
        if (!needed.isEmpty() && getActivity() != null) {
            getActivity().requestPermissions(needed.toArray(new String[0]), 0x10C0);
        }
        JSObject ret = new JSObject();
        ret.put("calls", hasPermission(Manifest.permission.READ_CALL_LOG));
        ret.put("sms", hasPermission(Manifest.permission.READ_SMS));
        call.resolve(ret);
    }

    @PluginMethod
    public void getNewCalls(PluginCall call) {
        JSArray calls = new JSArray();
        if (!hasPermission(Manifest.permission.READ_CALL_LOG)) {
            JSObject ret = new JSObject();
            ret.put("calls", calls);
            call.resolve(ret);
            return;
        }
        SharedPreferences prefs = prefs();
        long last = prefs.getLong(KEY_CALLS, 0L);
        long cutoff = (last == 0L) ? (System.currentTimeMillis() - FIRST_RUN_WINDOW_MS) : last;
        long maxSeen = last;
        try {
            Uri uri = CallLog.Calls.CONTENT_URI;
            String[] projection = new String[] {
                    CallLog.Calls._ID,
                    CallLog.Calls.NUMBER,
                    CallLog.Calls.CACHED_NAME,
                    CallLog.Calls.TYPE,
                    CallLog.Calls.DATE,
                    CallLog.Calls.DURATION
            };
            String selection = CallLog.Calls.DATE + " > ?";
            String[] args = new String[] { String.valueOf(cutoff) };
            Cursor c = bridge.getContext().getContentResolver().query(
                    uri, projection, selection, args, CallLog.Calls.DATE + " DESC");
            if (c != null) {
                int n = 0;
                while (c.moveToNext() && n < LIMIT) {
                    long date = c.getLong(c.getColumnIndexOrThrow(CallLog.Calls.DATE));
                    JSObject row = new JSObject();
                    row.put("id", c.getLong(c.getColumnIndexOrThrow(CallLog.Calls._ID)));
                    String number = c.getString(c.getColumnIndexOrThrow(CallLog.Calls.NUMBER));
                    if (number == null) number = "";
                    row.put("number", number);
                    String name = c.getString(c.getColumnIndexOrThrow(CallLog.Calls.CACHED_NAME));
                    if (name == null || name.isEmpty()) {
                        name = lookupContactName(number);
                    }
                    row.put("name", name == null ? "" : name);
                    int type = c.getInt(c.getColumnIndexOrThrow(CallLog.Calls.TYPE));
                    row.put("direction", callDirection(type));
                    row.put("at", date);
                    row.put("duration", c.getLong(c.getColumnIndexOrThrow(CallLog.Calls.DURATION)));
                    calls.put(row);
                    if (date > maxSeen) maxSeen = date;
                    n++;
                }
                c.close();
            }
        } catch (Exception e) {
            // Permission revoked or provider unavailable; return what we have.
        }
        if (maxSeen > last) {
            prefs.edit().putLong(KEY_CALLS, maxSeen).apply();
        }
        JSObject ret = new JSObject();
        ret.put("calls", calls);
        call.resolve(ret);
    }

    @PluginMethod
    public void getNewSms(PluginCall call) {
        JSArray messages = new JSArray();
        if (!hasPermission(Manifest.permission.READ_SMS)) {
            JSObject ret = new JSObject();
            ret.put("messages", messages);
            call.resolve(ret);
            return;
        }
        SharedPreferences prefs = prefs();
        long last = prefs.getLong(KEY_SMS, 0L);
        long cutoff = (last == 0L) ? (System.currentTimeMillis() - FIRST_RUN_WINDOW_MS) : last;
        long maxSeen = last;
        try {
            Uri uri = Uri.parse("content://sms");
            String[] projection = new String[] { "_id", "address", "body", "date", "type" };
            String selection = "date > ?";
            String[] args = new String[] { String.valueOf(cutoff) };
            Cursor c = bridge.getContext().getContentResolver().query(
                    uri, projection, selection, args, "date DESC");
            if (c != null) {
                int n = 0;
                while (c.moveToNext() && n < LIMIT) {
                    long date = c.getLong(c.getColumnIndexOrThrow("date"));
                    JSObject row = new JSObject();
                    row.put("id", c.getLong(c.getColumnIndexOrThrow("_id")));
                    String address = c.getString(c.getColumnIndexOrThrow("address"));
                    if (address == null) address = "";
                    row.put("number", address);
                    String name = lookupContactName(address);
                    row.put("name", name == null ? "" : name);
                    String body = c.getString(c.getColumnIndexOrThrow("body"));
                    row.put("body", body == null ? "" : body);
                    int type = c.getInt(c.getColumnIndexOrThrow("type"));
                    row.put("direction", smsDirection(type));
                    row.put("at", date);
                    messages.put(row);
                    if (date > maxSeen) maxSeen = date;
                    n++;
                }
                c.close();
            }
        } catch (Exception e) {
            // Provider unavailable; return what we have.
        }
        if (maxSeen > last) {
            prefs.edit().putLong(KEY_SMS, maxSeen).apply();
        }
        JSObject ret = new JSObject();
        ret.put("messages", messages);
        call.resolve(ret);
    }

    private boolean hasPermission(String perm) {
        return bridge.getContext().checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED;
    }

    private SharedPreferences prefs() {
        return bridge.getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private String callDirection(int type) {
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

    private String smsDirection(int type) {
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

    private String lookupContactName(String number) {
        if (number == null || number.isEmpty()) return "";
        if (!hasPermission(Manifest.permission.READ_CONTACTS)) return "";
        try {
            Uri uri = Uri.withAppendedPath(
                    ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number));
            Cursor c = bridge.getContext().getContentResolver().query(
                    uri, new String[] { ContactsContract.PhoneLookup.DISPLAY_NAME },
                    null, null, null);
            if (c != null) {
                if (c.moveToFirst()) {
                    String name = c.getString(0);
                    c.close();
                    return name == null ? "" : name;
                }
                c.close();
            }
        } catch (Exception e) {
            // Best-effort only.
        }
        return "";
    }
}
