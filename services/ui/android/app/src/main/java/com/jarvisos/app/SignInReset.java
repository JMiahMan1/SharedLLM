package com.jarvisos.app;

import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * What the background senders do when the server answers 401 to the stored key.
 *
 * An admin can sign everyone out and reset every key. A phone that kept the
 * old key in TokenBridge's mirror used to go on sending it: the location and
 * step services every few minutes, the push long poll every five, the widgets
 * on every refresh. None of them could ever succeed again, and nothing told
 * the person why their phone had gone quiet.
 *
 * Here the first 401 forgets the mirrored key, so every reader finds no key
 * and pauses (queued fixes, activity rows and health readings stay where they
 * are, for the next sign-in to send), and one notice asks the person to sign
 * in again. Only a 401 counts: a 403 is a key that works but may not do that
 * one thing, and a 5xx or a network error says nothing about the key.
 *
 * The key is forgotten only while it is still the stored one, so a sign-in
 * that happened while the request was in flight is never undone. The notice
 * is posted once per rejected key: the key's fingerprint is remembered, and
 * the widgets' re-sync from the app's own storage does not copy that key back
 * ({@link #wasRejected}). A new sign-in stores a new key, which works at once.
 */
public final class SignInReset {
    private static final String TAG = "SignInReset";
    /** SHA-256 of the last key the server refused (never the key itself). */
    static final String KEY_REJECTED = "jarvis_rejected_key";
    static final String MESSAGE = "Your sign-in was reset. Open Jarvis to sign in again.";
    private static final int NOTIFICATION_ID = 0x10C3;
    private static final Object LOCK = new Object();

    private SignInReset() {}

    /** Whether an HTTP status means the key itself was refused. */
    public static boolean isRejection(int status) {
        return status == 401;
    }

    /**
     * The server answered {@code status} to a request sent with {@code key}.
     * Anything but a 401 is ignored, so callers can pass every status.
     */
    public static void check(Context context, int status, String key) {
        if (isRejection(status)) keyRejected(context, key);
    }

    /** The server refused {@code key} with a 401: forget it and say so once. */
    public static void keyRejected(Context context, String key) {
        if (context == null || key == null || key.isEmpty()) return;
        Context app = context.getApplicationContext() != null ? context.getApplicationContext() : context;
        String fingerprint = fingerprint(key);
        boolean notify;
        synchronized (LOCK) {
            SharedPreferences p = TokenBridgePlugin.prefs(app);
            // Already forgotten, or somebody signed in meanwhile: nothing to undo.
            if (!key.equals(p.getString(TokenBridgePlugin.KEY_API_KEY, null))) return;
            notify = shouldNotify(p.getString(KEY_REJECTED, null), fingerprint);
            p.edit().remove(TokenBridgePlugin.KEY_API_KEY).putString(KEY_REJECTED, fingerprint).commit();
        }
        Log.w(TAG, "the server refused the stored key (401); background sync paused until the next sign-in");
        if (notify) postNotice(app);
    }

    /** Whether {@code key} is the one the server last refused (so it is not copied back in). */
    public static boolean wasRejected(Context context, String key) {
        if (key == null || key.isEmpty()) return false;
        String rejected = TokenBridgePlugin.prefs(context).getString(KEY_REJECTED, null);
        return rejected != null && rejected.equals(fingerprint(key));
    }

    /** A notice is due for a newly rejected key, not for one already announced. */
    static boolean shouldNotify(String lastRejected, String fingerprint) {
        return fingerprint != null && !fingerprint.equals(lastRejected);
    }

    /** Hex SHA-256 of the key, so the rejected key is recognised without being kept. */
    static String fingerprint(String key) {
        if (key == null || key.isEmpty()) return null;
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(key.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(digest.length * 2);
            for (byte b : digest) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            // Every Java runtime has SHA-256.
            return null;
        }
    }

    private static void postNotice(Context app) {
        try {
            PushNotifier.ensureChannels(app);
            NotificationManager nm = app.getSystemService(NotificationManager.class);
            if (nm == null) return;
            Intent open = new Intent(app, MainActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            PendingIntent tap = PendingIntent.getActivity(app, NOTIFICATION_ID, open,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            nm.notify(NOTIFICATION_ID, new NotificationCompat.Builder(app,
                    PushCategories.channelId(PushCategories.GENERAL, 3))
                    .setSmallIcon(PushCategories.icon(PushCategories.GENERAL, 3))
                    .setContentTitle("Sign in to Jarvis")
                    .setContentText(MESSAGE)
                    .setStyle(new NotificationCompat.BigTextStyle().bigText(MESSAGE))
                    .setAutoCancel(true)
                    .setContentIntent(tap)
                    .build());
        } catch (Exception e) {
            // No notification permission, or no notification service: the key is forgotten either way.
            Log.w(TAG, "could not post the sign-in notice: " + e.getMessage());
        }
    }
}
