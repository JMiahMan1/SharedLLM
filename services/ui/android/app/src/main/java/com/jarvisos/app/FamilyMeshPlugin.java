package com.jarvisos.app;

import android.Manifest;
import android.os.Build;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import org.json.JSONObject;

/**
 * Wander's offline family chat: turn it on (the only moment Android 12+ is
 * asked for Nearby devices' connect), send a message, a place or a small photo
 * preview, list what this phone holds, and the diagnostics a parent checks.
 */
@CapacitorPlugin(
    name = "JarvisFamilyMesh",
    permissions = {
        @Permission(alias = "connect", strings = { Manifest.permission.BLUETOOTH_CONNECT })
    }
)
public class FamilyMeshPlugin extends Plugin {

    @Override
    public void load() {
        FamilyMesh.setListener(() -> notifyListeners("change", new JSObject()));
    }

    private JSObject diag() {
        try {
            return JSObject.fromJSONObject(FamilyMesh.diagnostics(getContext()));
        } catch (Exception e) {
            return new JSObject();
        }
    }

    @PluginMethod
    public void getDiagnostics(PluginCall call) {
        call.resolve(diag());
    }

    @PluginMethod
    public void enable(PluginCall call) {
        FamilyMesh.setEnabled(getContext(), true);
        NearbyParts.ensure(getContext());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !FamilyMesh.connectPermission(getContext())) {
            requestPermissionForAlias("connect", call, "answered");
            return;
        }
        Proximity.mailChanged();
        call.resolve(diag());
    }

    @PermissionCallback
    private void answered(PluginCall call) {
        Proximity.mailChanged();
        call.resolve(diag());
    }

    @PluginMethod
    public void disable(PluginCall call) {
        FamilyMesh.setEnabled(getContext(), false);
        if (!NearbyParts.mesh(getContext())) FamilyMesh.closeServer();
        NearbyParts.ensure(getContext());
        Proximity.mailChanged();
        call.resolve(diag());
    }

    /** The family chat the messages go to once online (the one Chat opens on launch). */
    @PluginMethod
    public void setRoom(PluginCall call) {
        String room = call.getString("room", "");
        FamilyMesh.setRoom(getContext(), room != null && room.matches("[A-Za-z0-9]{4,64}") ? room : "");
        call.resolve();
    }

    @PluginMethod
    public void send(PluginCall call) {
        String kind = call.getString("kind", "text");
        JSObject fields = call.getObject("fields", new JSObject());
        try {
            String id = FamilyMesh.send(getContext(), kind, fields == null ? new JSONObject() : fields);
            JSObject o = new JSObject();
            o.put("id", id);
            call.resolve(o);
        } catch (IllegalArgumentException e) {
            call.reject(e.getMessage());
        } catch (Exception e) {
            call.reject("Could not keep that message");
        }
    }

    @PluginMethod
    public void list(PluginCall call) {
        JSObject o = new JSObject();
        try {
            o.put("messages", FamilyMesh.list(getContext()));
        } catch (Exception e) {
            o.put("messages", new JSArray());
        }
        call.resolve(o);
    }
}
