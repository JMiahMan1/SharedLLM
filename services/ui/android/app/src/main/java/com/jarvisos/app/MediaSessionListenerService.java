package com.jarvisos.app;

import android.app.Notification;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Grants Jarvis access to active media sessions ({@link MediaSessionPlugin})
 * and, on a protected child's phone with content monitoring enabled, forwards
 * incoming social/chat notifications ({@link SocialNotificationMonitor}) to the
 * child's activity feed so content monitoring and the weekly recap cover
 * Snapchat, Instagram, Discord, WhatsApp, and other messaging apps alongside SMS.
 */
public class MediaSessionListenerService extends NotificationListenerService {

    private final Map<String, Long> recentNotifications = new LinkedHashMap<>();

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null) return;
        Notification n = sbn.getNotification();
        if (n == null) return;
        Bundle extras = n.extras;
        if (extras == null) return;

        String pkg = sbn.getPackageName();
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
