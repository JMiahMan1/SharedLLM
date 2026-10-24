package com.jarvisos.app;

import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;

import androidx.core.app.NotificationCompat;

import java.util.HashSet;
import java.util.Set;

/**
 * What the screen guard tells the child on their own phone, as ordinary
 * notifications on the "Family safety" channel: the one-minute warning before
 * a lock, and "Ask a parent" when the web filter blocked a site. Also the
 * phone's list of user-installed apps for install approvals.
 */
final class GuardNotices {

    static final String CATEGORY = "family_safety";
    static final int WARNING_ID = 0x6A01;
    static final int SITE_ID = 0x6A02;

    private GuardNotices() {}

    /** The one-minute warning; it pops up (heads-up) and clears itself after the lock lands. */
    static void warn(Context context, String text) {
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        PushNotifier.ensureChannels(context);
        nm.notify(WARNING_ID, new NotificationCompat.Builder(context, PushCategories.channelId(CATEGORY, 4))
                .setSmallIcon(PushCategories.icon(CATEGORY, 4))
                .setContentTitle("One minute")
                .setContentText(text)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_REMINDER)
                .setTimeoutAfter(2 * 60_000L)
                .setAutoCancel(true)
                .build());
    }

    /** "<site> is blocked" with an "Ask a parent" button (AskParentReceiver sends the request). */
    static void offerSite(Context context, String site) {
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        PushNotifier.ensureChannels(context);
        Intent ask = new Intent(context, AskParentReceiver.class)
                .setAction(AskParentReceiver.ACTION_ASK_SITE)
                .putExtra(AskParentReceiver.EXTRA_SITE, site);
        PendingIntent onAsk = PendingIntent.getBroadcast(context, site.hashCode(), ask,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        nm.notify(SITE_ID, new NotificationCompat.Builder(context, PushCategories.channelId(CATEGORY, 3))
                .setSmallIcon(PushCategories.icon(CATEGORY, 3))
                .setContentTitle(site + " is blocked")
                .setContentText("Your parents can allow it for a while or for good.")
                .setOnlyAlertOnce(true)
                .setAutoCancel(true)
                .addAction(new NotificationCompat.Action.Builder(0, "Ask a parent", onAsk).build())
                .build());
    }

    /** The answer to tapping "Ask a parent": sent, or why not. */
    static void siteAsked(Context context, String site, String problem) {
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm == null) return;
        nm.notify(SITE_ID, new NotificationCompat.Builder(context, PushCategories.channelId(CATEGORY, 3))
                .setSmallIcon(PushCategories.icon(CATEGORY, 3))
                .setContentTitle(problem == null ? "Asked your parents" : "Could not ask")
                .setContentText(problem == null ? "You will hear when they answer about " + site + "." : problem)
                .setOnlyAlertOnce(true)
                .setAutoCancel(true)
                .build());
    }

    /** Apps the child installed (not part of the phone's system image). */
    static Set<String> userApps(Context context) {
        Set<String> out = new HashSet<>();
        PackageManager pm = context.getPackageManager();
        for (ApplicationInfo info : pm.getInstalledApplications(0)) {
            if ((info.flags & (ApplicationInfo.FLAG_SYSTEM | ApplicationInfo.FLAG_UPDATED_SYSTEM_APP)) == 0) {
                out.add(info.packageName);
            }
        }
        return out;
    }

    /** An app's name as the launcher shows it, or the package when unknown. */
    static String label(Context context, String pkg) {
        try {
            PackageManager pm = context.getPackageManager();
            return pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString();
        } catch (Exception e) {
            return pkg;
        }
    }
}
