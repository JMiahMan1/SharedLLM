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

    /**
     * Whether Jarvis's notices can show here: Android lets the app post at all
     * (Android 13's permission, or not switched off in settings) and the
     * family chat channel is not turned off. Without this a declined prompt
     * meant every notice was dropped and nobody could tell.
     */
    @PluginMethod
    public void notificationState(PluginCall call) {
        call.resolve(notificationState(getContext()));
    }

    static JSObject notificationState(android.content.Context context) {
        JSObject ret = new JSObject();
        boolean app = androidx.core.app.NotificationManagerCompat.from(context).areNotificationsEnabled();
        boolean chat = true;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            PushNotifier.ensureChannels(context);
            android.app.NotificationManager nm = context.getSystemService(android.app.NotificationManager.class);
            android.app.NotificationChannel channel = nm == null ? null
                    : nm.getNotificationChannel(PushCategories.channelId(PushCategories.CHAT, 3));
            chat = channel == null || channel.getImportance() != android.app.NotificationManager.IMPORTANCE_NONE;
        }
        ret.put("app", app);
        ret.put("chat", chat);
        return ret;
    }

    /** Android's notification settings for Jarvis, to switch them back on. */
    @PluginMethod
    public void openNotificationSettings(PluginCall call) {
        android.content.Intent intent = new android.content.Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, getContext().getPackageName())
                .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject("Could not open notification settings: " + e.getMessage());
        }
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

    /** The loaded plugin, so a nudge that arrives while Jarvis is open reaches the page. */
    private static volatile PushPlugin current;

    @Override
    public void load() {
        super.load();
        current = this;
    }

    /** A nudge arrived: the open page shows it (nothing is kept if no page listens). */
    static void nudged(String title, String body, String path, String kind) {
        PushPlugin plugin = current;
        if (plugin == null || !PushNotifier.appVisible) return;
        JSObject data = new JSObject();
        data.put("title", title);
        data.put("body", body);
        data.put("path", path);
        data.put("kind", kind == null ? "" : kind);
        plugin.notifyListeners("nudge", data);
    }

    /** A notification was tapped: tell JS where to go (kept until it listens). */
    void open(String path) {
        // Kept as well as announced. A tap that starts the app cold opens the
        // page before the bundle has attached its listener, and a WebView
        // reload loses it again, so the page asks for it on mount.
        hold(path);
        JSObject data = new JSObject();
        data.put("path", path);
        notifyListeners("open", data, true);
    }

    /** The page a tap asked for before this WebView was listening; null when there is none. */
    private static volatile String pendingPath;

    @PluginMethod
    public void takePendingOpen(PluginCall call) {
        String path = takePending();
        JSObject ret = new JSObject();
        if (path != null) ret.put("path", path);
        call.resolve(ret);
    }

    /** The held page, and only once: the caller owns it from here. */
    static String takePending() {
        String path = pendingPath;
        pendingPath = null;
        return path;
    }

    /** An empty path is not a destination: a tap with nothing to open keeps the one already held. */
    static void hold(String path) {
        if (path != null && !path.isEmpty()) pendingPath = path;
    }

    /** For the JVM tests: a tap, through the same door a real one takes. */
    static void rememberOpen(String path) {
        hold(path);
    }
}
