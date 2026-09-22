package com.jarvisos.app.widgets;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class WidgetBootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        String a = intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(a)
            || Intent.ACTION_LOCKED_BOOT_COMPLETED.equals(a)
            || "android.intent.action.MY_PACKAGE_REPLACED".equals(a)
            || "android.intent.action.QUICKBOOT_POWERON".equals(a)) {
            Context app = context.getApplicationContext();
            WidgetUpdater.requestAll(app);
            WidgetAlarm.schedule(app);
        }
    }
}
