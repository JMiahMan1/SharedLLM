package com.jarvisos.app;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.HashSet;
import java.util.Set;

/**
 * What the screen guard tells the child on their own phone, as ordinary
 * Jarvis notices (PushNotice through PushNotifier, the "Family safety"
 * channel): the one-minute warning before a lock, and "… is blocked" with an
 * "Ask a parent" button, a push callback ({@code guard_ask}) the server runs
 * like any other notification button. Also the phone's user-installed apps,
 * for install approvals.
 */
final class GuardNotices {

    static final String CATEGORY = "family_safety";

    private GuardNotices() {}

    /** The one-minute warning (heads-up, one at a time). */
    static void warn(Context context, String text) {
        show(context, notice("guard-warning", "One minute", text, 4, null));
    }

    /** "<site> is blocked" with "Ask a parent"; tapping it sends the request as the child. */
    static void offerSite(Context context, String site) {
        try {
            JSONObject ask = new JSONObject()
                    .put("label", "Ask a parent")
                    .put("type", PushNotice.Action.CALLBACK)
                    .put("callback", "guard_ask")
                    .put("params", new JSONObject().put("kind", "site").put("target", site));
            show(context, notice("guard-site:" + site, site + " is blocked",
                    "Your parents can allow it for a while or for good.", 3, new JSONArray().put(ask)));
        } catch (Exception ignored) {
            // A notice we cannot build is not shown; browsing is unaffected.
        }
    }

    private static PushNotice notice(String tag, String title, String body, int priority, JSONArray actions) {
        try {
            JSONObject o = new JSONObject()
                    .put("category", CATEGORY)
                    .put("tag", tag)
                    .put("title", title)
                    .put("body", body)
                    .put("priority", priority);
            if (actions != null) o.put("actions", actions);
            return PushNotice.parse(o.toString());
        } catch (Exception e) {
            return null;
        }
    }

    private static void show(Context context, PushNotice n) {
        if (n != null) PushNotifier.show(context, n);
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
}
