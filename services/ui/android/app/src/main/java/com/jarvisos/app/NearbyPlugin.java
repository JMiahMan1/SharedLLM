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

import java.util.List;

/**
 * Settings > Nearby & offline on this phone: read the parts and apply what
 * Settings (or a parent, for a child) chose. Android 12+ is asked for Nearby
 * devices only when a part that needs it has just been turned on and only
 * with {@code ask: true} (the person's own tap); Android 9 to 11 ask nothing.
 */
@CapacitorPlugin(
    name = "JarvisNearby",
    permissions = {
        @Permission(alias = "radio", strings = {
            Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_ADVERTISE
        }),
        @Permission(alias = "connect", strings = { Manifest.permission.BLUETOOTH_CONNECT })
    }
)
public class NearbyPlugin extends Plugin {

    private JSObject state() {
        try {
            return JSObject.fromJSONObject(NearbyParts.state(getContext()));
        } catch (Exception e) {
            return new JSObject();
        }
    }

    @PluginMethod
    public void getState(PluginCall call) {
        call.resolve(state());
    }

    /** {nearby, chat, offline, car, ask}: save, start or stop the engine, ask for what a new part needs. */
    @PluginMethod
    public void apply(PluginCall call) {
        android.content.Context c = getContext();
        NearbyRules.Parts before = NearbyParts.parts(c);
        NearbyRules.Parts after = NearbyRules.merge(before, call.getData(), NearbyParts.child(c));
        NearbyParts.save(c, after);
        boolean ask = Boolean.TRUE.equals(call.getBoolean("ask", false));
        List<String> need = NearbyRules.toAsk(before, after, Build.VERSION.SDK_INT,
                Proximity.scanPermission(c), Proximity.advertisePermission(c), FamilyMesh.connectPermission(c));
        if (ask && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (need.contains("scan") || need.contains("advertise")) {
                call.getData().put("_connect", need.contains("connect"));
                requestPermissionForAlias("radio", call, "radioAnswered");
                return;
            }
            if (need.contains("connect")) {
                requestPermissionForAlias("connect", call, "answered");
                return;
            }
        }
        done(call);
    }

    /** Ask for whatever the parts that are on still lack (the "Allow Nearby devices" button). */
    @PluginMethod
    public void requestPermissions(PluginCall call) {
        android.content.Context c = getContext();
        NearbyRules.Parts none = new NearbyRules.Parts(false, false, false, false, false);
        List<String> need = NearbyRules.toAsk(none, NearbyParts.parts(c), Build.VERSION.SDK_INT,
                Proximity.scanPermission(c), Proximity.advertisePermission(c), FamilyMesh.connectPermission(c));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && (need.contains("scan") || need.contains("advertise"))) {
            call.getData().put("_connect", need.contains("connect"));
            requestPermissionForAlias("radio", call, "radioAnswered");
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && need.contains("connect")) {
            requestPermissionForAlias("connect", call, "answered");
            return;
        }
        done(call);
    }

    @PermissionCallback
    private void radioAnswered(PluginCall call) {
        if (Boolean.TRUE.equals(call.getBoolean("_connect", false)) && !FamilyMesh.connectPermission(getContext())) {
            requestPermissionForAlias("connect", call, "answered");
            return;
        }
        done(call);
    }

    @PermissionCallback
    private void answered(PluginCall call) {
        done(call);
    }

    private void done(PluginCall call) {
        NearbyParts.ensure(getContext());
        DeviceHealth.reportAsync(getContext());
        call.resolve(state());
    }
}
