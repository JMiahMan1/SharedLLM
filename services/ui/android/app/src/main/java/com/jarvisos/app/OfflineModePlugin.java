package com.jarvisos.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONObject;

/**
 * Offline mode for the web app: queue a Jarvis request for a family phone that
 * is online, read the household's cached information, send a parent's command
 * to a child's phone over Bluetooth, and the diagnostics. Uses only what the
 * offline chat already turned on; asks for nothing.
 */
@CapacitorPlugin(name = "JarvisOfflineMode")
public class OfflineModePlugin extends Plugin {

    private static final long PEER_RECENT_MS = 5 * 60_000L;

    @PluginMethod
    public void queue(PluginCall call) {
        String kind = call.getString("kind", "ask");
        JSObject body = call.getObject("body", new JSObject());
        try {
            JSObject o = new JSObject();
            o.put("id", OfflineMode.queue(getContext(), kind, body == null ? new JSONObject() : body));
            call.resolve(o);
        } catch (IllegalArgumentException e) {
            call.reject(e.getMessage());
        } catch (Exception e) {
            call.reject("Could not queue that");
        }
    }

    @PluginMethod
    public void requests(PluginCall call) {
        try {
            JSObject o = new JSObject();
            o.put("requests", OfflineMode.myRequests(getContext(), Proximity.heardWithin(PEER_RECENT_MS),
                    FamilyMesh.online(getContext())));
            o.put("online", FamilyMesh.online(getContext()));
            o.put("peerNearby", Proximity.heardWithin(PEER_RECENT_MS));
            call.resolve(o);
        } catch (Exception e) {
            call.reject("Could not read the queue");
        }
    }

    @PluginMethod
    public void cache(PluginCall call) {
        try {
            JSObject o = new JSObject();
            o.put("items", OfflineMode.cache(getContext()));
            call.resolve(o);
        } catch (Exception e) {
            call.reject("Could not read the saved information");
        }
    }

    @PluginMethod
    public void sendCommand(PluginCall call) {
        String child = call.getString("child", "");
        String action = call.getString("action", "");
        Integer minutes = call.getInt("minutes", 0);
        if (child == null || child.isEmpty() || !java.util.Arrays.asList("lock", "unlock", "bonus").contains(action)) {
            call.reject("child and action (lock, unlock, bonus) are required");
            return;
        }
        try {
            JSObject o = new JSObject();
            o.put("id", OfflineMode.sendCommand(getContext(), child, action, minutes == null ? 0 : Math.max(0, minutes)));
            call.resolve(o);
        } catch (IllegalArgumentException e) {
            call.reject(e.getMessage());
        } catch (Exception e) {
            call.reject("Could not sign that command on this phone");
        }
    }

    @PluginMethod
    public void commands(PluginCall call) {
        try {
            JSObject o = new JSObject();
            o.put("commands", OfflineMode.myCommands(getContext()));
            call.resolve(o);
        } catch (Exception e) {
            call.reject("Could not read the commands");
        }
    }

    @PluginMethod
    public void getDiagnostics(PluginCall call) {
        try {
            JSObject o = JSObject.fromJSONObject(OfflineMode.diagnostics(getContext()));
            o.put("online", FamilyMesh.online(getContext()));
            o.put("peerNearby", Proximity.heardWithin(PEER_RECENT_MS));
            call.resolve(o);
        } catch (Exception e) {
            call.reject("Could not read offline mode");
        }
    }
}
