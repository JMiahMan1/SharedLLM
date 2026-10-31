package com.jarvisos.app;

import android.app.Notification;
import android.content.ComponentName;
import android.content.Context;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import com.getcapacitor.JSObject;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Grants Jarvis access to active media sessions ({@link MediaSessionPlugin}),
 * and, because Android keeps a notification listener running whether or not
 * the app is open, collects two things for the activity feed:
 *
 * <ul>
 * <li>on a protected child's phone with content monitoring on, incoming chat
 *     and social notifications ({@link SocialNotificationMonitor}), so content
 *     monitoring and the weekly recap cover Snapchat, Instagram, Discord,
 *     WhatsApp and other messaging apps alongside SMS;</li>
 * <li>what other apps start playing (YouTube, Spotify, podcasts), once per
 *     title while it plays. The WebView used to poll for this only while
 *     Jarvis was open.</li>
 * </ul>
 *
 * Both go through {@link ActivityOutbox}, so nothing is lost while offline.
 */
public class MediaSessionListenerService extends NotificationListenerService {

    private final Map<String, Long> recentNotifications = new LinkedHashMap<>();

    private MediaSessionManager sessionManager;
    private MediaSessionManager.OnActiveSessionsChangedListener sessionsListener;
    private final Map<MediaController, MediaController.Callback> callbacks = new HashMap<>();
    /** Titles reported as playing; one leaves when it stops, so a replay is reported again. */
    private final Set<String> playingKeys = new HashSet<>();

    @Override
    public void onListenerConnected() {
        super.onListenerConnected();
        try {
            sessionManager = (MediaSessionManager) getSystemService(Context.MEDIA_SESSION_SERVICE);
            ComponentName me = new ComponentName(this, MediaSessionListenerService.class);
            sessionsListener = this::watch;
            sessionManager.addOnActiveSessionsChangedListener(sessionsListener, me);
            watch(sessionManager.getActiveSessions(me));
        } catch (Exception e) {
            // Access withdrawn between connecting and asking: nothing to watch.
            sessionManager = null;
        }
    }

    @Override
    public void onListenerDisconnected() {
        if (sessionManager != null && sessionsListener != null) {
            try {
                sessionManager.removeOnActiveSessionsChangedListener(sessionsListener);
            } catch (Exception ignored) {
                // Already gone with the connection.
            }
        }
        watch(null);
        super.onListenerDisconnected();
    }

    /** Follow exactly these sessions' playback (and stop following the rest). */
    private void watch(List<MediaController> controllers) {
        for (Map.Entry<MediaController, MediaController.Callback> e : callbacks.entrySet()) {
            e.getKey().unregisterCallback(e.getValue());
        }
        callbacks.clear();
        if (controllers == null) return;
        for (MediaController controller : new ArrayList<>(controllers)) {
            MediaController.Callback cb = new MediaController.Callback() {
                @Override
                public void onPlaybackStateChanged(PlaybackState state) {
                    noticePlayback(controller);
                }

                @Override
                public void onMetadataChanged(MediaMetadata metadata) {
                    noticePlayback(controller);
                }
            };
            controller.registerCallback(cb);
            callbacks.put(controller, cb);
            noticePlayback(controller);
        }
    }

    /** Report a title once when it starts playing; forget it when it stops. */
    private void noticePlayback(MediaController controller) {
        JSObject session = MediaSessionPlugin.readSession(controller);
        if (session == null) return;
        String app = session.optString("app", "");
        if ("com.jarvisos.app".equals(app)) return; // Jarvis's own player reports itself
        String key = app + "|" + session.optString("title", "");
        if (!session.optBoolean("playing", false)) {
            playingKeys.remove(key);
            return;
        }
        if (!playingKeys.add(key)) return;
        try {
            JSONObject detail = new JSONObject()
                .put("app", app)
                .put("artist", session.optString("artist", ""))
                .put("album", session.optString("album", ""))
                .put("playing", true)
                .put("source", "external");
            JSONObject body = new JSONObject()
                .put("feature", "media")
                .put("event", "play")
                .put("item", session.optString("title", ""))
                .put("detail", detail);
            ActivityOutbox.add(this, body);
        } catch (Exception ignored) {
            // A play that cannot be described is not reported.
        }
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null) return;
        Notification n = sbn.getNotification();
        if (n == null) return;
        Bundle extras = n.extras;
        if (extras == null) return;

        String pkg = sbn.getPackageName();
        boolean conversation = extras.containsKey(Notification.EXTRA_MESSAGES)
                || "android.app.Notification$MessagingStyle".equals(extras.getString(Notification.EXTRA_TEMPLATE));
        if (!SocialNotificationMonitor.isSocial(pkg, n.category, conversation)) return;
        boolean ongoing = sbn.isOngoing() || (n.flags & Notification.FLAG_ONGOING_EVENT) != 0;
        boolean groupSummary = (n.flags & Notification.FLAG_GROUP_SUMMARY) != 0;
        CharSequence titleSeq = extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE);
        if (titleSeq == null || titleSeq.length() == 0) {
            titleSeq = extras.getCharSequence(Notification.EXTRA_TITLE);
        }
        CharSequence textSeq = extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
        if (textSeq == null || textSeq.length() == 0) {
            textSeq = extras.getCharSequence(Notification.EXTRA_TEXT);
        }

        SocialNotificationMonitor.Record record;
        synchronized (recentNotifications) {
            record = SocialNotificationMonitor.extract(
                    GuardApi.isProtected(this),
                    GuardApi.monitoringMode(this),
                    pkg,
                    ongoing,
                    groupSummary,
                    n.category,
                    titleSeq == null ? "" : titleSeq.toString(),
                    textSeq == null ? "" : textSeq.toString(),
                    recentNotifications,
                    System.currentTimeMillis());
        }
        if (record != null) {
            String label = UnifiedPushDistributor.label(this, record.pkg);
            GuardApi.reportSocialNotification(this, record.pkg, label, record.sender, record.body);
        }
    }
}
