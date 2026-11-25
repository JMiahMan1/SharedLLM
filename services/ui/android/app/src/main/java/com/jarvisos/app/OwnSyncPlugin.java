package com.jarvisos.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * My devices for the web app: the person's own data as this device holds it
 * (their devices, the latest fix, today's steps by the daily-maximum rule, the
 * watch), the diagnostics (each other device, when it last synced, and per kind
 * "In sync" or "Updated from"), and the watch's stats handed over when this
 * phone read them. Uses only what Settings > Nearby & offline turned on.
 */
@CapacitorPlugin(name = "JarvisOwnSync")
public class OwnSyncPlugin extends Plugin {

    @PluginMethod
    public void state(PluginCall call) {
        try {
            call.resolve(JSObject.fromJSONObject(OwnSync.state(getContext())));
        } catch (Exception e) {
            call.reject("Could not read your devices");
        }
    }

    @PluginMethod
    public void getDiagnostics(PluginCall call) {
        try {
            call.resolve(JSObject.fromJSONObject(OwnSync.diagnostics(getContext())));
        } catch (Exception e) {
            call.reject("Could not read your devices");
        }
    }

    /** {stats: the watch's 6e400004 JSON, name?}: shared with the person's other devices. */
    @PluginMethod
    public void noteWatch(PluginCall call) {
        JSObject stats = call.getObject("stats", null);
        if (stats == null) {
            call.reject("stats is required");
            return;
        }
        try {
            OwnSync.noteWatch(getContext(), stats, call.getString("name", ""));
            call.resolve();
        } catch (Exception e) {
            call.reject("Could not keep the watch's stats");
        }
    }
}
