package com.jarvisos.app;

import android.content.Intent;
import android.os.Build;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Starts and stops {@link StepSyncService}.
 *
 * Step counting is independent of location consent. The WebView still reads
 * the sensor while the app is open, but this service is what keeps the ledger
 * current when the app is closed and location sharing is off.
 */
@CapacitorPlugin(name = "StepSync")
public class StepSyncPlugin extends Plugin {

    @PluginMethod
    public void start(PluginCall call) {
        String username = call.getString("username");
        if (username == null || username.trim().isEmpty()) {
            call.reject("username is required to sync steps");
            return;
        }
        StepSyncService.setEnabled(getContext(), true);
        StepSyncService.setStepsEnabled(getContext(), true);
        Intent intent = new Intent(getContext(), StepSyncService.class);
        intent.putExtra("username", username.trim());
        String userId = call.getString("userId");
        if (userId != null) {
            intent.putExtra("userId", userId);
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                getContext().startForegroundService(intent);
            } else {
                getContext().startService(intent);
            }
            JSObject ret = new JSObject();
            ret.put("started", true);
            call.resolve(ret);
        } catch (Exception e) {
            call.reject(e.getMessage() != null ? e.getMessage() : "Could not start step sync", e);
        }
    }

    @PluginMethod
    public void stop(PluginCall call) {
        StepSyncService.setEnabled(getContext(), false);
        StepSyncService.setStepsEnabled(getContext(), false);
        Intent intent = new Intent(getContext(), StepSyncService.class);
        intent.setAction("stop");
        try {
            getContext().startService(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject(e.getMessage() != null ? e.getMessage() : "Could not stop step sync", e);
        }
    }

    @PluginMethod
    public void isRunning(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("running", StepSyncService.isEnabled(getContext()));
        call.resolve(ret);
    }
}
