package com.jarvisos.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;
import com.jarvisos.app.widgets.WidgetAlarm;
import com.jarvisos.app.widgets.WidgetUpdater;

/**
 * Puts the app's background work back after a reboot or an app update.
 *
 * Both wipe what was running: alarms are cleared and services are not
 * restarted. Widgets came back (this was WidgetBootReceiver), but location
 * sharing and chat notifications stayed off until someone opened the app,
 * which for a phone that is never opened meant indefinitely.
 */
public class BootReceiver extends BroadcastReceiver {
    private static final String TAG = "BootReceiver";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        String a = intent.getAction();
        boolean updated = Intent.ACTION_MY_PACKAGE_REPLACED.equals(a);
        if (!updated
                && !Intent.ACTION_BOOT_COMPLETED.equals(a)
                && !"android.intent.action.QUICKBOOT_POWERON".equals(a)) {
            return;
        }
        Context app = context.getApplicationContext();
        WidgetUpdater.requestAll(app);
        WidgetAlarm.schedule(app);
        try {
            // A reboot clears alarms: put back a crash countdown or SOS retries.
            CrashAlert.resume(app);
            LocationTrackingService.resumeIfEnabled(app);
            StepSyncService.resumeIfEnabled(app);
            PushService.start(app);
            BleBeacon.ensure(app);
        } catch (Exception e) {
            Log.w(TAG, "could not resume background work after " + a, e);
        }
        // Never brought back to the front after an update: an update happens
        // in the background with nothing on screen (the owner's rule). The
        // services above resume by themselves.
    }
}
