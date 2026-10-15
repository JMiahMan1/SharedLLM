package com.jarvisos.app;

import android.Manifest;
import android.os.Build;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * JS side of {@link ChatNotifyService}: start it once signed in (asking for
 * Android 13's notification permission first), say which conversation is on
 * screen, and hear about notification taps so the app opens the right page.
 */
@CapacitorPlugin(
    name = "ChatNotify",
    permissions = { @Permission(strings = { Manifest.permission.POST_NOTIFICATIONS }, alias = "notifications") }
)
public class ChatNotifyPlugin extends Plugin {

    @PluginMethod
    public void start(PluginCall call) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && getPermissionState("notifications") != PermissionState.GRANTED) {
            requestPermissionForAlias("notifications", call, "afterPermission");
            return;
        }
        startService(call);
    }

    @PermissionCallback
    private void afterPermission(PluginCall call) {
        // Started either way: notices still reach the in-app list, and the
        // person can allow notifications later in Android's settings.
        startService(call);
    }

    private void startService(PluginCall call) {
        ChatNotifyService.start(getContext());
        JSObject ret = new JSObject();
        ret.put("notifications", Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
                || getPermissionState("notifications") == PermissionState.GRANTED);
        call.resolve(ret);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        ChatNotifyService.stop(getContext());
        call.resolve();
    }

    @PluginMethod
    public void setActiveConversation(PluginCall call) {
        String token = call.getString("token");
        ChatNotifyService.conversationOpened(getContext(), token == null || token.isEmpty() ? null : token);
        call.resolve();
    }

    /** A notification was tapped: tell JS where to go (kept until it listens). */
    void open(String path) {
        JSObject data = new JSObject();
        data.put("path", path);
        notifyListeners("open", data, true);
    }
}
