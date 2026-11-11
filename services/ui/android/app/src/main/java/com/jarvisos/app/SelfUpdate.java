package com.jarvisos.app;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.os.Build;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Jarvis installing a newer Jarvis by itself.
 *
 * The APK goes through a {@link PackageInstaller} session instead of the
 * "open this .apk" hand-off. On Android 12+ a session that updates the app
 * doing the installing needs no tap once Jarvis is its own installer of
 * record ({@code USER_ACTION_NOT_REQUIRED}); the first time, or on older
 * Android, Android still asks, and {@link Receiver} shows that screen (or a
 * notification to tap when the app is in the background).
 *
 * Android's install screen names "Jarvis OS" just like the uninstall dialog,
 * which Jarvis Guard covers on a child's phone. While an update Jarvis
 * started is in progress ({@link #inProgress}) the guard lets the install
 * screen through (GuardRules.isOwnUpdateScreen); the uninstall dialog stays
 * covered. That is what used to need a parent's ten-minute unlock.
 *
 * Android itself refuses an update not signed with the app's own key, so the
 * window cannot be used to install anything else over Jarvis.
 */
final class SelfUpdate {
    private static final String TAG = "SelfUpdate";
    static final String ACTION_STATUS = "com.jarvisos.app.SELF_UPDATE_STATUS";
    private static final String KEY_UNTIL = "guard_self_update_until";
    /** Long enough to tap Install (and for Play Protect's scan); short enough to not linger. */
    static final long WINDOW_MS = 5 * 60_000L;
    private static final String CHANNEL = "jarvis_app_update";
    private static final int NOTIFICATION_ID = 0x5E1F;

    static final String EXTRA_CONFIRM = "jarvis_self_update_confirm";

    private SelfUpdate() {}

    /** The "tap to finish" notification opened Jarvis: show Android's install screen. */
    static void maybeContinue(android.app.Activity activity, Intent intent) {
        if (intent == null || !intent.hasExtra(EXTRA_CONFIRM)) return;
        Intent confirm = intent.getParcelableExtra(EXTRA_CONFIRM);
        intent.removeExtra(EXTRA_CONFIRM);
        if (confirm == null) return;
        open(activity);
        try {
            activity.startActivity(confirm);
        } catch (Exception e) {
            close(activity);
            Log.w(TAG, "could not open the installer: " + e.getMessage());
        }
    }

    /** An update Jarvis started is waiting on Android's installer. */
    static boolean inProgress(Context context) {
        return System.currentTimeMillis() < GuardApi.prefs(context).getLong(KEY_UNTIL, 0L);
    }

    private static void open(Context context) {
        GuardApi.prefs(context).edit().putLong(KEY_UNTIL, System.currentTimeMillis() + WINDOW_MS).apply();
    }

    private static void close(Context context) {
        GuardApi.prefs(context).edit().remove(KEY_UNTIL).apply();
    }

    /** Hand {@code apk} (already verified) to Android's installer. True when the session was committed. */
    static boolean install(Context context, File apk) {
        if (apk == null || !apk.exists()) return false;
        PackageInstaller.Session session = null;
        try {
            PackageInstaller installer = context.getPackageManager().getPackageInstaller();
            PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(
                    PackageInstaller.SessionParams.MODE_FULL_INSTALL);
            params.setAppPackageName(context.getPackageName());
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                params.setPackageSource(PackageInstaller.PACKAGE_SOURCE_OTHER);
            }
            int sessionId = installer.createSession(params);
            session = installer.openSession(sessionId);
            try (InputStream in = new FileInputStream(apk);
                 OutputStream out = session.openWrite("jarvis.apk", 0, apk.length())) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                session.fsync(out);
            }
            int flags = PendingIntent.FLAG_UPDATE_CURRENT
                    | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ? PendingIntent.FLAG_MUTABLE : 0);
            Intent status = new Intent(context, Receiver.class).setAction(ACTION_STATUS);
            PendingIntent pi = PendingIntent.getBroadcast(context, sessionId, status, flags);
            open(context);
            session.commit(pi.getIntentSender());
            session.close();
            Log.i(TAG, "update session " + sessionId + " committed");
            return true;
        } catch (Exception e) {
            Log.w(TAG, "session install failed: " + e.getMessage());
            if (session != null) {
                try {
                    session.abandon();
                } catch (Exception ignored) {
                    // best effort
                }
            }
            close(context);
            return false;
        }
    }

    /** Android's answer for the session: ask the person, or it is done. */
    public static class Receiver extends BroadcastReceiver {
        @Override
        public void onReceive(Context context, Intent intent) {
            int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
            if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
                if (confirm == null) return;
                open(context); // the person may take a moment to tap Install
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                try {
                    context.startActivity(confirm);
                } catch (Exception e) {
                    // In the background Android may refuse to open it: ask with a notification.
                    notifyTapToUpdate(context, confirm);
                }
                return;
            }
            close(context);
            if (status == PackageInstaller.STATUS_SUCCESS) {
                Log.i(TAG, "updated");
            } else {
                Log.w(TAG, "update not installed (" + status + "): "
                        + intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE));
            }
        }
    }

    private static void notifyTapToUpdate(Context context, Intent confirm) {
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(new NotificationChannel(
                    CHANNEL, "App updates", NotificationManager.IMPORTANCE_DEFAULT));
        }
        int flags = PendingIntent.FLAG_UPDATE_CURRENT
                | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0);
        // Through Jarvis, so the guard's window opens again when it is tapped
        // (maybe much later), not only when the notification was posted.
        Intent viaJarvis = new Intent(context, MainActivity.class)
                .putExtra(EXTRA_CONFIRM, confirm)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent tap = PendingIntent.getActivity(context, NOTIFICATION_ID, viaJarvis, flags);
        nm.notify(NOTIFICATION_ID, new NotificationCompat.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.mi_notifications)
                .setContentTitle("Jarvis OS update ready")
                .setContentText("Tap to finish installing it.")
                .setContentIntent(tap)
                .setAutoCancel(true)
                .build());
    }
}
