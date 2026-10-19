package com.jarvisos.app;

import android.Manifest;
import android.os.Build;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * JS side of {@link PushService}: start it once signed in (asking for
 * Android 13's notification permission first), say which conversation is on
 * screen, hear about notification taps so the app opens the right page, and
 * manage the apps using Jarvis for UnifiedPush.
 *
 * Still named "ChatNotify" in JS: the web bundle updates on its own, and a new
 * bundle on an older APK (or the reverse) must find the plugin it expects.
 */
@CapacitorPlugin(
    name = "ChatNotify",
    permissions = { @Permission(strings = { Manifest.permission.POST_NOTIFICATIONS }, alias = "notifications") }
)
public class PushPlugin extends Plugin {

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
        PushService.start(getContext());
        JSObject ret = new JSObject();
        ret.put("notifications", Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
                || getPermissionState("notifications") == PermissionState.GRANTED);
        call.resolve(ret);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        PushService.stop(getContext());
        call.resolve();
    }

    @PluginMethod
    public void ringAlarm(PluginCall call) {
        String title = call.getString("title", "Alarm");
        String body = call.getString("body", "Time is up!");
        FindPhoneService.startAlarm(getContext(), title, body);
        call.resolve();
    }

    @PluginMethod
    public void stopAlarm(PluginCall call) {
        FindPhoneService.stop(getContext());
        call.resolve();
    }

    @PluginMethod
    public void setActiveConversation(PluginCall call) {
        String token = call.getString("token");
        PushNotifier.conversationOpened(getContext(), token == null || token.isEmpty() ? null : token);
        call.resolve();
    }

    /** The names people know apps by, for the "Apps using Jarvis for push" list. */
    @PluginMethod
    public void appLabels(PluginCall call) {
        JSArray packages = call.getArray("packages", new JSArray());
        JSObject labels = new JSObject();
        for (int i = 0; i < packages.length(); i++) {
            String pkg = packages.optString(i, "");
            if (!pkg.isEmpty()) labels.put(pkg, UnifiedPushDistributor.label(getContext(), pkg));
        }
        JSObject ret = new JSObject();
        ret.put("labels", labels);
        call.resolve(ret);
    }

    /**
     * The person removed an app from the list: the app hears UNREGISTERED and
     * the gateway forgets the registration. {@code removed} is 0 when the
     * registration was not made on this phone.
     */
    @PluginMethod
    public void removeApp(PluginCall call) {
        final String app = call.getString("app", "");
        final String token = call.getString("token", "");
        if ((app == null || app.isEmpty()) && (token == null || token.isEmpty())) {
            call.reject("app or token required");
            return;
        }
        UnifiedPushDistributor.run(() -> {
            int removed = UnifiedPushDistributor.remove(getContext(), app, token);
            JSObject ret = new JSObject();
            ret.put("removed", removed);
            call.resolve(ret);
        });
    }

    /** A notification was tapped: tell JS where to go (kept until it listens). */
    void open(String path) {
        JSObject data = new JSObject();
        data.put("path", path);
        notifyListeners("open", data, true);
    }
}
