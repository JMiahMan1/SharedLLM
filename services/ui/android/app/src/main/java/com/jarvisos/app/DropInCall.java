package com.jarvisos.app;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.telecom.PhoneAccount;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;
import android.util.Log;

/**
 * Family Drop In while the app is in the background or the phone is locked.
 *
 * A ring arrives as a "call" notice on {@link PushService}. When the person
 * lets the caller drop in (their list, on the server) and this device's
 * drop-in switch is on, the phone answers it by itself, like a walkie-talkie: it registers a
 * self-managed call with Android's Telecom and answers it at once
 * ({@link DropInConnectionService}). Being a real call is what lets the app
 * use the microphone from the background ({@link DropInService} runs as a
 * phoneCall/microphone foreground service). The WebView's existing call code
 * then joins the room: told directly through {@link DropInPlugin} when the
 * app is alive, or opened on the call page over the lock screen when it is not.
 *
 * Telecom refuses a new call while the person is on a phone call; then the
 * ordinary "X is calling" notice is shown instead, nothing barges in.
 */
final class DropInCall {
    private static final String TAG = "DropInCall";
    static final String EXTRA_ROOM = "jarvis_drop_in_room";
    static final String EXTRA_NAME = "jarvis_drop_in_name";
    static final String EXTRA_DROP_IN = "jarvis_drop_in";
    private static final String ACCOUNT_ID = "jarvis-drop-in";

    /** The call Telecom is holding for us, or null. Main thread only. */
    static volatile DropInConnectionService.DropInConnection current;

    private DropInCall() {}

    /**
     * This device answers drop-ins by itself: on unless switched off. The web
     * app copies the server's per-device switch here (lib/callSettings.ts),
     * so a parent's change on a child's phone holds in the background too.
     */
    static boolean enabled(Context context) {
        SharedPreferences cap = context.getSharedPreferences("CapacitorStorage", Context.MODE_PRIVATE);
        return !"0".equals(cap.getString("jarvis_drop_in_device", null));
    }

    /**
     * The ring lets this person's devices answer by themselves: the person
     * (or a parent) put the caller on their drop-in list. The server marks
     * it in the notice's link ({@code &drop_in=1}); any other ring just rings.
     */
    static boolean allowedByRing(PushNotice notice) {
        if (notice == null || notice.path == null) return false;
        return "1".equals(Uri.parse("jarvis://open" + notice.path).getQueryParameter("drop_in"));
    }

    /** The room a call notice rings, from its {@code /family?tab=calls&answer=<room>} link. */
    static String roomOf(PushNotice notice) {
        if (notice == null || !"call".equals(notice.category) || notice.path == null) return null;
        String room = Uri.parse("jarvis://open" + notice.path).getQueryParameter("answer");
        return room != null && room.matches("[A-Za-z0-9_-]{1,64}") ? room : null;
    }

    /**
     * Answer a ringing call by itself when the ring allows it and this device has drop-in on.
     * True when it took the call over (the ringing notice is then not shown).
     */
    static boolean maybeAutoJoin(Context context, PushNotice notice) {
        String room = roomOf(notice);
        if (room == null || !allowedByRing(notice) || !enabled(context)) return false;
        if (current != null) return false; // already in a drop-in: let this one ring
        String name = notice.title == null ? "Family call" : notice.title;
        try {
            TelecomManager telecom = context.getSystemService(TelecomManager.class);
            if (telecom == null) return false;
            PhoneAccountHandle handle = account(context, telecom);
            if (!telecom.isIncomingCallPermitted(handle)) {
                Log.i(TAG, "a call is in progress; ringing " + room + " instead");
                return false;
            }
            Bundle extras = new Bundle();
            extras.putString(EXTRA_ROOM, room);
            extras.putString(EXTRA_NAME, name);
            Bundle wrapper = new Bundle();
            wrapper.putBundle(TelecomManager.EXTRA_INCOMING_CALL_EXTRAS, extras);
            telecom.addNewIncomingCall(handle, wrapper);
            Log.i(TAG, "dropping in to " + room);
            return true;
        } catch (Exception e) {
            // No Telecom (some tablets), or it said no: the notice still rings.
            Log.w(TAG, "could not drop in: " + e.getMessage());
            return false;
        }
    }

    private static PhoneAccountHandle account(Context context, TelecomManager telecom) {
        PhoneAccountHandle handle = new PhoneAccountHandle(
                new ComponentName(context, DropInConnectionService.class), ACCOUNT_ID);
        if (telecom.getPhoneAccount(handle) == null) {
            telecom.registerPhoneAccount(PhoneAccount.builder(handle, "Jarvis Drop In")
                    .setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED)
                    .build());
        }
        return handle;
    }

    /** Telecom answered: join the room in the WebView. */
    static void join(Context context, String room, String name) {
        DropInService.start(context, room, name);
        if (DropInPlugin.join(room, name)) return;
        // No WebView running: open the call page (over the lock screen).
        Intent intent = openIntent(context, room);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            context.startActivity(intent);
        } catch (Exception e) {
            // A background start can be refused; the ongoing call
            // notification's full-screen intent opens it instead.
            Log.w(TAG, "could not open the call page: " + e.getMessage());
        }
    }

    static Intent openIntent(Context context, String room) {
        String path = "/family?tab=calls&answer=" + room;
        return new Intent(context, MainActivity.class)
                .setAction(Intent.ACTION_VIEW)
                .setData(Uri.parse("jarvis://open" + path))
                .putExtra(PushNotifier.EXTRA_OPEN_PATH, path)
                .putExtra(EXTRA_DROP_IN, true)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
    }

    /** The call is over (hung up here, in the app, or by Telecom). */
    static void end(Context context) {
        DropInConnectionService.DropInConnection connection = current;
        current = null;
        if (connection != null) connection.finish();
        DropInService.stop(context);
    }

    /** Hang up from the notification: leave the room, then end the call. */
    static void hangUp(Context context) {
        DropInPlugin.hangUp();
        end(context);
    }

    static boolean isActive() {
        return current != null;
    }
}
