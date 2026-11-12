package com.jarvisos.app;

import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * The web app's side of a background drop-in ({@link DropInCall}): native
 * says {@code join} (Telecom answered a ring) or {@code hangup} (Hang up on
 * the call notification); the web app says {@code ended} when its call is
 * over so the Telecom call and the foreground service end with it.
 */
@CapacitorPlugin(name = "DropIn")
public class DropInPlugin extends Plugin {
    private static volatile DropInPlugin instance;

    @Override
    public void load() {
        instance = this;
    }

    @Override
    protected void handleOnDestroy() {
        if (instance == this) instance = null;
    }

    /** Ask the running WebView to join {@code room}; false when there is none. */
    static boolean join(String room, String name) {
        DropInPlugin plugin = instance;
        if (plugin == null || plugin.getBridge() == null) return false;
        JSObject data = new JSObject();
        data.put("room", room);
        data.put("name", name);
        plugin.notifyListeners("join", data, true);
        return true;
    }

    static void hangUp() {
        DropInPlugin plugin = instance;
        if (plugin != null) plugin.notifyListeners("hangup", new JSObject(), false);
    }

    /** The web app's call ended (or never started): end the Telecom call. */
    @PluginMethod
    public void ended(PluginCall call) {
        new Handler(Looper.getMainLooper()).post(() -> {
            DropInCall.end(getContext());
            // The call page was allowed over the lock screen for the call only;
            // Jarvis opened from the screen-time lock keeps its own hold, so
            // MainActivity decides what is left (LockedJarvisRules).
            android.app.Activity activity = getActivity();
            if (activity instanceof MainActivity) {
                ((MainActivity) activity).dropInEnded();
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1 && activity != null) {
                activity.setShowWhenLocked(false);
                activity.setTurnScreenOn(false);
            }
        });
        call.resolve();
    }

    @PluginMethod
    public void status(PluginCall call) {
        JSObject result = new JSObject();
        result.put("active", DropInCall.isActive());
        call.resolve(result);
    }
}
