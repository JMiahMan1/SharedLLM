package com.jarvisos.app;

import android.view.WindowManager;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * FLAG_SECURE on the app window while the parents' private chat is open: no
 * screenshots, no screen recording, and a blank recent-apps thumbnail. The
 * page turns it on when the chat opens and off when it closes
 * (src/plugins/secureWindow.ts); leaving the app with the chat open keeps it
 * on, which is what keeps the chat out of the recents list.
 */
@CapacitorPlugin(name = "JarvisSecureWindow")
public class SecureWindowPlugin extends Plugin {

    @PluginMethod
    public void enable(PluginCall call) {
        set(call, true);
    }

    @PluginMethod
    public void disable(PluginCall call) {
        set(call, false);
    }

    private void set(PluginCall call, boolean secure) {
        getActivity().runOnUiThread(() -> {
            if (secure) {
                getActivity().getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
            } else {
                getActivity().getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
            }
            JSObject result = new JSObject();
            result.put("secure", secure);
            call.resolve(result);
        });
    }
}
