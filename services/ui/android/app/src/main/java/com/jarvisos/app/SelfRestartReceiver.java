package com.jarvisos.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * Relaunches the app after its own APK has been installed.
 *
 * A runtime-registered receiver cannot do this: installing a package over the
 * running one kills our process, so anything registered in code is gone before
 * the broadcast arrives. It has to be declared in the manifest so the receiver
 * is created fresh against the newly-installed manifest.
 *
 * The system installer's own "Open" button also starts the app, so this is a
 * convenience rather than the only way back in. If an OEM build swallows the
 * broadcast, nothing is lost.
 */
public class SelfRestartReceiver extends BroadcastReceiver {
    private static final String TAG = "SelfRestart";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())) {
            return;
        }
        // ACTION_MY_PACKAGE_REPLACED is delivered only to the package that was
        // just replaced, and unlike ACTION_PACKAGE_REPLACED it cannot be
        // spoofed by another app. There is no data URI to compare, so the
        // action alone is the signal.
        String self = context.getPackageName();
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(self);
        if (launch == null) {
            Log.w(TAG, "No launch intent for " + self);
            return;
        }
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        Log.i(TAG, "Relaunching " + self + " after update");
        context.startActivity(launch);
    }
}
