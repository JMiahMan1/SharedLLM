package com.jarvisos.app;

import android.app.ActivityManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.provider.Settings;
import android.text.TextUtils;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.util.List;

/**
 * What other apps are playing right now (YouTube, Spotify, podcasts, browser
 * media). Backed by the system's media sessions, which Android only exposes to
 * an app that has been granted Notification access. We never prompt for it; the
 * person flips the toggle in Settings, exactly like Usage access.
 */
@CapacitorPlugin(name = "MediaSession")
public class MediaSessionPlugin extends Plugin {

    @PluginMethod
    public void hasAccess(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("granted", hasNotificationAccess());
        call.resolve(ret);
    }

    @PluginMethod
    public void openSettings(PluginCall call) {
        Intent intent = new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        bridge.getContext().startActivity(intent);
        call.resolve();
    }

    /**
     * Whether this APK reports what other apps play by itself
     * ({@link MediaSessionListenerService}); the WebView then stops doing it,
     * so nothing is reported twice. An older APK has no such method.
     */
    @PluginMethod
    public void nativeReports(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("media", true);
        call.resolve(ret);
    }

    @PluginMethod
    public void getActive(PluginCall call) {
        JSArray sessions = new JSArray();
        if (hasNotificationAccess()) {
            MediaSessionManager manager =
                    (MediaSessionManager) bridge.getContext().getSystemService(Context.MEDIA_SESSION_SERVICE);
            ComponentName listener =
                    new ComponentName(bridge.getContext(), MediaSessionListenerService.class);
            try {
                List<MediaController> controllers = manager.getActiveSessions(listener);
                if (controllers != null) {
                    for (MediaController controller : controllers) {
                        JSObject session = readSession(controller);
                        if (session != null) {
                            sessions.put(session);
                        }
                    }
                }
            } catch (Exception e) {
                // Access can be revoked between the check and the read; treat as empty.
            }
        }
        JSObject ret = new JSObject();
        ret.put("sessions", sessions);
        call.resolve(ret);
    }

    private boolean hasNotificationAccess() {
        try {
            String enabled = Settings.Secure.getString(
                    bridge.getContext().getContentResolver(),
                    "enabled_notification_listeners");
            if (TextUtils.isEmpty(enabled)) {
                return false;
            }
            ComponentName listener =
                    new ComponentName(bridge.getContext(), MediaSessionListenerService.class);
            return enabled.contains(listener.flattenToString());
        } catch (Exception e) {
            return false;
        }
    }

    static JSObject readSession(MediaController controller) {
        if (controller == null) {
            return null;
        }
        PlaybackState state = controller.getPlaybackState();
        MediaMetadata meta = controller.getMetadata();
        if (meta == null && state == null) {
            return null;
        }
        JSObject session = new JSObject();
        String pkg = controller.getPackageName();
        session.put("app", pkg == null ? "" : pkg);
        String title = meta != null ? meta.getString(MediaMetadata.METADATA_KEY_TITLE) : null;
        session.put("title", title == null ? "" : title);
        String artist = meta != null ? meta.getString(MediaMetadata.METADATA_KEY_ARTIST) : null;
        if (artist == null && meta != null) {
            artist = meta.getString(MediaMetadata.METADATA_KEY_ALBUM_ARTIST);
        }
        session.put("artist", artist == null ? "" : artist);
        String album = meta != null ? meta.getString(MediaMetadata.METADATA_KEY_ALBUM) : null;
        session.put("album", album == null ? "" : album);
        long duration = meta != null ? meta.getLong(MediaMetadata.METADATA_KEY_DURATION) : 0L;
        session.put("durationMs", Math.max(0L, duration));
        long position = state != null ? state.getPosition() : 0L;
        session.put("positionMs", Math.max(0L, position));
        int playback = state != null ? state.getState() : PlaybackState.STATE_NONE;
        session.put("playing", playback == PlaybackState.STATE_PLAYING);
        // Skip sessions that expose nothing useful.
        if (session.optString("title", "").isEmpty()
                && session.optString("artist", "").isEmpty()) {
            return null;
        }
        return session;
    }
}
