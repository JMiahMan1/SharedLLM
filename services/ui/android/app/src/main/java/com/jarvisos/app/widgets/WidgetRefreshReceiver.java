package com.jarvisos.app.widgets;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class WidgetRefreshReceiver extends BroadcastReceiver {
    public static final String ACTION = "com.jarvisos.app.widget.TICK";

    @Override
    public void onReceive(Context context, Intent intent) {
        Context app = context.getApplicationContext();
        WidgetUpdater.requestAll(app);
        WidgetAlarm.schedule(app);
    }
}
