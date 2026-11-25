package com.jarvisos.app;

import android.Manifest;
import android.os.Build;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * Wander's "Find family nearby" on this device: turn it on (the only moment
 * Android 12+ is asked for Nearby devices: scan and advertise; Android 9 to 11
 * scan with the location permission Wander already holds), turn it off, and
 * the diagnostics a parent checks on a real phone.
 */
@CapacitorPlugin(
    name = "JarvisProximity",
    permissions = {
        @Permission(alias = "nearby", strings = {
            Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_ADVERTISE
        })
    }
)
public class ProximityPlugin extends Plugin {

    private JSObject diag() {
        try {
            return JSObject.fromJSONObject(Proximity.diagnostics(getContext()));
        } catch (Exception e) {
            return new JSObject();
        }
    }

    @PluginMethod
    public void getDiagnostics(PluginCall call) {
        JSObject o = diag();
        o.put("apiLevel", Build.VERSION.SDK_INT);
        call.resolve(o);
    }

    @PluginMethod
    public void enable(PluginCall call) {
        Proximity.setEnabled(getContext(), true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !Proximity.scanPermission(getContext())) {
            requestPermissionForAlias("nearby", call, "answered");
            return;
        }
        NearbyParts.ensure(getContext());
        DeviceHealth.reportAsync(getContext());
        call.resolve(diag());
    }

    @PermissionCallback
    private void answered(PluginCall call) {
        NearbyParts.ensure(getContext());
        DeviceHealth.reportAsync(getContext());
        call.resolve(diag());
    }

    @PluginMethod
    public void disable(PluginCall call) {
        Proximity.setEnabled(getContext(), false);
        NearbyParts.ensure(getContext());
        DeviceHealth.reportAsync(getContext());
        call.resolve(diag());
    }
}
