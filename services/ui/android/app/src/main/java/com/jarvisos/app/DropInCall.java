package com.jarvisos.app;

import android.app.KeyguardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.PixelFormat;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.telecom.PhoneAccount;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;

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

    /** How long a Drop In taken by itself may take to really connect before it rings instead. */
    static final long CONNECT_WITHIN_MS = 15_000;
    /** The room the app said it is in (DropInPlugin.joined); null when none. */
    static volatile String joinedRoom;

    /**
     * Take a Drop In by itself, or ring it - never neither.
     *
     * <p>A ring the caller may drop in with ({@code drop_in=1}) on a device with Drop In on joins by
     * itself: a Drop In left over from before (the app was closed, so it cannot still be in it) is ended
     * first, and when Android's call system will not take it the phone joins without it. Anything that
     * would stop it connecting by itself makes it ring as an ordinary call instead: Drop In switched off
     * here, an unlocked phone whose app is closed and may not open over other apps (Android would only
     * show a notification), or a join the app has not confirmed within {@link #CONNECT_WITHIN_MS}.
     * True when it took the call (the ringing notice is then not shown now). Every outcome is reported
     * to the server (DropInReport), so the gateway's log says what the phone did and why.
     */
    static boolean maybeAutoJoin(Context context, PushNotice notice) {
        String room = roomOf(notice);
        if (room == null || !allowedByRing(notice)) return false; // not a Drop In: an ordinary call rings
        if (!enabled(context)) {
            DropInReport.send(context, room, "rang", "Drop In is switched off on this device");
            return false;
        }
        boolean appRunning = DropInPlugin.hasWebView();
        if (!appRunning && !isLocked(context) && !canOpenOverOtherApps(context)) {
            // The one case Android leaves no way to answer: the setup step (Drop In settings) was never
            // finished. Ringing beats a call that cannot connect.
            DropInReport.send(context, room, "rang",
                    "Drop In setup unfinished: Jarvis may not display over other apps, the phone is unlocked and Jarvis is closed");
            return false;
        }
        String name = notice.title == null ? "Family call" : notice.title;
        DropInConnectionService.DropInConnection previous = current;
        if (previous != null) {
            if (room.equals(previous.room()) && appRunning) return true; // already in this one
            Log.i(TAG, "ending a Drop In left over from before to take " + room);
            end(context);
        }
        joinedRoom = null;
        String how = "android-call";
        try {
            TelecomManager telecom = context.getSystemService(TelecomManager.class);
            PhoneAccountHandle handle = telecom == null ? null : account(context, telecom);
            if (telecom != null && telecom.isIncomingCallPermitted(handle)) {
                Bundle extras = new Bundle();
                extras.putString(EXTRA_ROOM, room);
                extras.putString(EXTRA_NAME, name);
                Bundle wrapper = new Bundle();
                wrapper.putBundle(TelecomManager.EXTRA_INCOMING_CALL_EXTRAS, extras);
                telecom.addNewIncomingCall(handle, wrapper);
            } else {
                how = "direct (Android's call system was busy)";
                ding();
                join(context, room, name);
            }
        } catch (Exception e) {
            // No Telecom (some tablets) or it said no: join without it.
            Log.w(TAG, "joining without Telecom: " + e.getMessage());
            how = "direct (" + e.getClass().getSimpleName() + ")";
            ding();
            join(context, room, name);
        }
        Log.i(TAG, "dropping in to " + room + " via " + how);
        DropInReport.send(context, room, "answering", how);
        ringUnlessJoined(context.getApplicationContext(), notice, room);
        return true;
    }

    /** The safety net: a Drop In the app has not confirmed joining in time ends and rings instead. */
    private static void ringUnlessJoined(Context context, PushNotice notice, String room) {
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            if (room.equals(joinedRoom)) return;
            Log.w(TAG, "Drop In " + room + " did not connect by itself; ringing instead");
            end(context);
            try {
                PushNotifier.show(context, notice);
            } catch (Exception e) {
                Log.w(TAG, "could not ring: " + e.getMessage());
            }
            DropInReport.send(context, room, "rang", "did not connect by itself within "
                    + (CONNECT_WITHIN_MS / 1000) + " s");
        }, CONNECT_WITHIN_MS);
    }

    /** The app is in the Drop In (DropInPlugin.joined). */
    static void markJoined(String room) {
        joinedRoom = room;
    }

    private static boolean isLocked(Context context) {
        KeyguardManager keyguard = context.getSystemService(KeyguardManager.class);
        return keyguard != null && keyguard.isKeyguardLocked();
    }

    /** The Drop In sound: one short ding as the call connects (the only sound a Drop In makes). */
    static void ding() {
        try {
            ToneGenerator tone = new ToneGenerator(AudioManager.STREAM_NOTIFICATION, 90);
            tone.startTone(ToneGenerator.TONE_PROP_BEEP2, 350);
            new Handler(Looper.getMainLooper()).postDelayed(tone::release, 600);
        } catch (Exception e) {
            Log.w(TAG, "no ding: " + e.getMessage());
        }
    }

    /**
     * Whether Jarvis may open the call while another app is on screen: Android lets an app start its own
     * screen from the background only with "Display over other apps". Without it an unlocked phone shows
     * the call as a notification to tap (a locked one still opens it over the lock screen).
     */
    static boolean canOpenOverOtherApps(Context context) {
        return Settings.canDrawOverlays(context);
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

    /** Telecom answered (or the phone joins without it): join the room in the WebView. */
    static void join(Context context, String room, String name) {
        DropInService.start(context, room, name);
        if (DropInPlugin.join(room, name)) return;
        // No WebView running: open the call page (over the lock screen, or over another app).
        openCallPage(context.getApplicationContext(), room);
    }

    /**
     * Open the call page from the background. Android lets an app do that only while it shows a window
     * over other apps (Android 15 and later require the window to be visible, not just the permission),
     * so with "Display over other apps" a one-pixel, untouchable overlay is shown for the moment of the
     * start and then removed. A locked phone opens it over the lock screen either way (the ongoing call
     * notification's full-screen intent, and MainActivity's show-when-locked for a Drop In).
     */
    private static void openCallPage(Context context, String room) {
        Intent intent = openIntent(context, room).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Handler main = new Handler(Looper.getMainLooper());
        main.post(() -> {
            WindowManager windows = context.getSystemService(WindowManager.class);
            View overlay = null;
            if (windows != null && canOpenOverOtherApps(context)) {
                try {
                    overlay = new View(context);
                    WindowManager.LayoutParams lp = new WindowManager.LayoutParams(1, 1,
                            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                                    | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                                    | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                            PixelFormat.TRANSLUCENT);
                    windows.addView(overlay, lp);
                } catch (Exception e) {
                    Log.w(TAG, "could not show the overlay that lets the call open: " + e.getMessage());
                    overlay = null;
                }
            }
            try {
                context.startActivity(intent);
            } catch (Exception e) {
                Log.w(TAG, "could not open the call page: " + e.getMessage());
            }
            View shown = overlay;
            if (shown != null) {
                main.postDelayed(() -> {
                    try {
                        windows.removeView(shown);
                    } catch (Exception ignored) {
                        // Already gone.
                    }
                }, 3000);
            }
        });
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
