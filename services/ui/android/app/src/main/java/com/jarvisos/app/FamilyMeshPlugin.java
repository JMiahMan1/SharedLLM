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
 * The offline chat, in every chat room (Chat): turn it on (the only moment
 * Android 12+ is asked for Nearby devices' connect), the rooms that work
 * offline, send a message, a place or a small photo preview to a room, list
 * what this phone holds and may read, and the diagnostics a parent checks.
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

    /** The rooms this person can use offline (names only, never the keys), as last fetched. */
    @PluginMethod
    public void rooms(PluginCall call) {
        JSObject o = new JSObject();
        try {
            o.put("rooms", MeshRules.roomsForApp(FamilyMesh.rooms(getContext())));
        } catch (Exception e) {
            o.put("rooms", new JSArray());
        }
        call.resolve(o);
    }

    /** Fetch the rooms and their keys again now (online; the chat list changed). */
    @PluginMethod
    public void refreshRooms(PluginCall call) {
        new Thread(() -> {
            try {
                FamilyMesh.refreshRooms(getContext());
                call.resolve();
            } catch (Exception e) {
                call.reject("Could not get your chats for offline use");
            }
        }, "family-mesh-rooms").start();
    }

    @PluginMethod
    public void send(PluginCall call) {
        String room = call.getString("room", "");
        String kind = call.getString("kind", "text");
        JSObject fields = call.getObject("fields", new JSObject());
        try {
            String id = FamilyMesh.send(getContext(), room, kind, fields == null ? new JSONObject() : fields);
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
            o.put("messages", FamilyMesh.list(getContext(), call.getString("room", "")));
        } catch (Exception e) {
            o.put("messages", new JSArray());
        }
        call.resolve(o);
    }
}
