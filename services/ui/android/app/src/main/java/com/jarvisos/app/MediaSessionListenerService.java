package com.jarvisos.app;

import android.service.notification.NotificationListenerService;

/**
 * Exists so the system grants Jarvis access to active media sessions. The
 * actual reading happens in {@link MediaSessionPlugin}; this service only needs
 * to be enabled by the person in Settings → Apps → Special access →
 * Notification access. Without it {@code MediaSessionManager.getActiveSessions}
 * returns nothing.
 */
public class MediaSessionListenerService extends NotificationListenerService {
    // Intentionally empty: the plugin queries MediaSessionManager on demand.
}
