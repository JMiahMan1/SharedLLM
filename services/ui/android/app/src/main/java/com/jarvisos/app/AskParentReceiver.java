package com.jarvisos.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * "Ask a parent" on the web filter's blocked-site notification: sends the
 * request as the phone's signed-in child and says whether it went. The
 * parents see it on their parents' page and allow the site for a while, for
 * good, or not.
 */
public class AskParentReceiver extends BroadcastReceiver {

    static final String ACTION_ASK_SITE = "com.jarvisos.app.ASK_PARENT_SITE";
    static final String EXTRA_SITE = "site";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !ACTION_ASK_SITE.equals(intent.getAction())) return;
        String site = intent.getStringExtra(EXTRA_SITE);
        if (site == null || site.isEmpty()) return;
        PendingResult pending = goAsync();
        Context app = context.getApplicationContext();
        new Thread(() -> {
            try {
                GuardNotices.siteAsked(app, site, GuardApi.askParent(app, "site", site, null));
            } finally {
                pending.finish();
            }
        }).start();
    }
}
