package com.jarvisos.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * The crash notification's "I'm OK" and "Get help", and the alarms for the
 * end of the countdown and each retry ({@link CrashAlert}). The send is a
 * network call, so it runs on its own thread with the broadcast kept open.
 */
public class CrashAlertReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        final String action = intent.getAction();
        final Context app = context.getApplicationContext();
        final PendingResult pending = goAsync();
        new Thread(() -> {
            try {
                CrashAlert.handle(app, action);
            } finally {
                pending.finish();
            }
        }, "crash-alert").start();
    }
}
