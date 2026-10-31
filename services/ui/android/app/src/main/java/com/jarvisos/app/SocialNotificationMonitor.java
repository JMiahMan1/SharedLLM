package com.jarvisos.app;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

/**
 * Notification/social monitoring on a child's phone (Bark-style): decides
 * whether a posted Android notification carries an incoming message or social
 * update that should be recorded on the child's activity feed for content
 * monitoring and the weekly recap.
 *
 * Plain Java with no Android types, so every case is unit-tested on the JVM.
 */
public final class SocialNotificationMonitor {

    static final long DEDUPE_MS = 10 * 60_000L;
    static final int MAX_RECENT = 200;
    static final int MAX_BODY_CHARS = 500;
    static final int MAX_SENDER_CHARS = 120;

    /**
     * Packages whose notifications are never recorded here: Jarvis itself,
     * system plumbing, and the SMS apps (which {@link CommsPlugin} already
     * reads directly from the SMS store).
     */
    static final Set<String> IGNORED_PACKAGES = new HashSet<>(Arrays.asList(
            "com.jarvisos.app",
            "android",
            "com.android.systemui",
            "com.android.settings",
            "com.android.vending",
            "com.google.android.gms",
            "com.google.android.dialer",
            "com.android.dialer",
            "com.samsung.android.dialer",
            "com.google.android.apps.messaging",
            "com.android.mms",
            "com.samsung.android.messaging"));

    /** Notification categories that are system status or media transport, not social messages. */
    static final Set<String> IGNORED_CATEGORIES = new HashSet<>(Arrays.asList(
            "transport", "service", "sys", "progress", "status", "alarm", "Stopwatch"));

    /**
     * Messaging and social apps whose notifications are messages even when the
     * app does not label them so. Anything else counts only when Android says
     * it is a message ({@link #isSocial}).
     */
    static final Set<String> SOCIAL_PACKAGES = new HashSet<>(Arrays.asList(
            "com.snapchat.android",
            "com.instagram.android",
            "com.instagram.barcelona",
            "com.discord",
            "com.whatsapp",
            "com.facebook.orca",
            "com.facebook.katana",
            "com.zhiliaoapp.musically",
            "com.ss.android.ugc.trill",
            "org.telegram.messenger",
            "org.thoughtcrime.securesms",
            "kik.android",
            "com.groupme.android",
            "com.reddit.frontpage",
            "com.twitter.android",
            "com.roblox.client",
            "com.google.android.apps.dynamite",
            "com.microsoft.teams"));

    /** Notification categories Android uses for a message from a person. */
    static final Set<String> SOCIAL_CATEGORIES = new HashSet<>(Arrays.asList("msg", "social"));

    /**
     * Whether a notification is a message or social update at all: from a
     * known messaging or social app, in a message/social category, or drawn
     * as a conversation (MessagingStyle). Every other app's notifications
     * (games, shopping, video, news) used to be recorded as "social", which
     * filled the feed and the recap with noise.
     */
    public static boolean isSocial(String pkg, String category, boolean conversation) {
        if (pkg != null && SOCIAL_PACKAGES.contains(pkg)) return true;
        if (category != null && SOCIAL_CATEGORIES.contains(category)) return true;
        return conversation;
    }

    public static final class Record {
        public final String pkg;
        public final String sender;
        public final String body;

        Record(String pkg, String sender, String body) {
            this.pkg = pkg;
            this.sender = sender;
            this.body = body;
        }
    }

    private SocialNotificationMonitor() {}

    /**
     * Returns a {@link Record} to post to {@code /api/activity} when the
     * notification is a real user-facing message on a watched minor's phone
     * and has not already been reported in the last {@link #DEDUPE_MS}, or
     * {@code null} when it should be ignored.
     */
    public static Record extract(boolean protectedPhone, String monitoring, String pkg,
                                 boolean ongoing, boolean groupSummary, String category,
                                 String title, String text,
                                 Map<String, Long> recent, long nowMs) {
        if (!protectedPhone || "off".equals(monitoring) || ongoing || groupSummary) return null;
        if (pkg == null || pkg.isEmpty() || IGNORED_PACKAGES.contains(pkg)) return null;
        if (category != null && IGNORED_CATEGORIES.contains(category)) return null;

        String cleanBody = clean(text, MAX_BODY_CHARS);
        if (cleanBody.isEmpty()) return null;
        String cleanSender = clean(title, MAX_SENDER_CHARS);

        if (recent != null) {
            prune(recent, nowMs);
            String key = pkg + "|" + cleanSender + "|" + cleanBody;
            Long last = recent.get(key);
            if (last != null && nowMs - last < DEDUPE_MS) return null;
            if (recent.size() >= MAX_RECENT) {
                Iterator<String> it = recent.keySet().iterator();
                if (it.hasNext()) {
                    it.next();
                    it.remove();
                }
            }
            recent.put(key, nowMs);
        }
        return new Record(pkg, cleanSender, cleanBody);
    }

    private static void prune(Map<String, Long> recent, long nowMs) {
        Iterator<Map.Entry<String, Long>> it = recent.entrySet().iterator();
        while (it.hasNext()) {
            Map.Entry<String, Long> e = it.next();
            if (e.getValue() == null || nowMs - e.getValue() >= DEDUPE_MS) {
                it.remove();
            }
        }
    }

    private static String clean(String value, int maxLen) {
        if (value == null) return "";
        String collapsed = value.trim().replaceAll("\\s+", " ");
        return collapsed.length() > maxLen ? collapsed.substring(0, maxLen) : collapsed;
    }
}
