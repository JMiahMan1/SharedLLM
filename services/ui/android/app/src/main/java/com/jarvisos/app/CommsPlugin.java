package com.jarvisos.app;

import android.Manifest;
import android.content.Context;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Calls and SMS: the permission check and prompt for reading the device's
 * call log and SMS store, and {@code syncNow}, which sends new ones to the
 * family profile through {@link CommsCollector} (the background worker runs
 * the same pass while the app is closed). Runtime permissions (READ_CALL_LOG,
 * READ_SMS) are required; contacts are resolved best-effort for names.
 */
@CapacitorPlugin(name = "Comms")
public class CommsPlugin extends Plugin {

    private static final int ACCESS_REQUEST_CODE = 0x10C0;
    /** The call waiting on the permission dialog, resolved from its result. */
    private PluginCall pendingAccessCall;

    @PluginMethod
    public void hasAccess(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("calls", hasPerm(Manifest.permission.READ_CALL_LOG));
        ret.put("sms", hasPerm(Manifest.permission.READ_SMS));
        call.resolve(ret);
    }

    @PluginMethod
    public void requestAccess(PluginCall call) {
        List<String> needed = new ArrayList<>();
        if (!hasPerm(Manifest.permission.READ_CALL_LOG)) {
            needed.add(Manifest.permission.READ_CALL_LOG);
        }
        if (!hasPerm(Manifest.permission.READ_SMS)) {
            needed.add(Manifest.permission.READ_SMS);
        }
        if (!hasPerm(Manifest.permission.READ_CONTACTS)) {
            needed.add(Manifest.permission.READ_CONTACTS);
        }
        if (!needed.isEmpty() && getActivity() != null) {
            // The answer arrives after the dialog closes. Resolving here
            // reported the state from *before* it, so a caller could believe
            // access was refused the moment after the person granted it.
            pendingAccessCall = call;
            getActivity().requestPermissions(needed.toArray(new String[0]), ACCESS_REQUEST_CODE);
            return;
        }
        resolveAccess(call);
    }

    @Override
    public void handleRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.handleRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != ACCESS_REQUEST_CODE) return;
        PluginCall call = pendingAccessCall;
        pendingAccessCall = null;
        if (call == null || call.isReleased()) return;
        resolveAccess(call);
    }

    private void resolveAccess(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("calls", hasPerm(Manifest.permission.READ_CALL_LOG));
        ret.put("sms", hasPerm(Manifest.permission.READ_SMS));
        call.resolve(ret);
    }

    /**
     * Send new calls and texts now ({@link CommsCollector}, the same pass the
     * background worker runs), off the main thread. Resolves with how many of
     * each were sent; the WebView calls this on its timer while Jarvis is open.
     */
    @PluginMethod
    public void syncNow(PluginCall call) {
        Context app = bridge.getContext().getApplicationContext();
        new Thread(() -> {
            JSONObject result = CommsCollector.run(app);
            JSObject ret = new JSObject();
            ret.put("ok", result.optBoolean("ok", false));
            ret.put("calls", result.optInt("calls", 0));
            ret.put("sms", result.optInt("sms", 0));
            call.resolve(ret);
        }).start();
    }

    private boolean hasPerm(String perm) {
        return CommsCollector.hasPerm(bridge.getContext(), perm);
    }
}
