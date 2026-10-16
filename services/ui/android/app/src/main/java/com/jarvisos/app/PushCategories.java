package com.jarvisos.app;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * The notice categories from docs/PUSH.md and the Android channel each one
 * uses, so people can tune every kind of notice in the system settings as
 * well as in Jarvis's own Settings.
 *
 * Free of Android imports so the mapping runs in plain JVM tests;
 * {@link PushNotifier} turns these into NotificationChannels.
 */
final class PushCategories {
    static final String CHAT = "chat";
    static final String GENERAL = "general";
    static final String URGENT = "urgent";

    static final String CHANNEL_PREFIX = "jarvis_";

    /** Channel importance, mapped onto NotificationManager's levels by the notifier. */
    static final int QUIET = 2;
    static final int NORMAL = 3;
    static final int POP_UP = 4;

    static final String GROUP_FAMILY = "family";
    static final String GROUP_REMINDERS = "reminders";
    static final String GROUP_HOME = "home_and_system";

    static final class Category {
        final String id;
        final String name;
        final String description;
        final int importance;
        final String group;

        Category(String id, String name, String description, int importance, String group) {
            this.id = id;
            this.name = name;
            this.description = description;
            this.importance = importance;
            this.group = group;
        }

        String channelId() {
            return CHANNEL_PREFIX + id;
        }
    }

    static final List<Category> ALL = Collections.unmodifiableList(Arrays.asList(
        new Category(CHAT, "Family chat",
            "New messages and Jarvis answers in your conversations.", POP_UP, GROUP_FAMILY),
        new Category("family_presence", "Arrivals and departures",
            "When family arrive at or leave a saved place.", NORMAL, GROUP_FAMILY),
        new Category("family_safety", "Family safety",
            "Low phone battery, school arrivals, long stops somewhere unknown and On my way.",
            POP_UP, GROUP_FAMILY),
        new Category("health", "Health and fitness",
            "Step goals reached and your weekly summary.", QUIET, GROUP_FAMILY),
        new Category("leave_by", "Time to leave",
            "When to head out to reach your next event on time.", POP_UP, GROUP_REMINDERS),
        new Category("calendar", "Calendar reminders",
            "Before events on your calendars.", NORMAL, GROUP_REMINDERS),
        new Category("timers", "Timers and alarms",
            "When a timer or alarm goes off.", POP_UP, GROUP_REMINDERS),
        new Category("bible", "Bible reading",
            "Reminders for your reading plan.", NORMAL, GROUP_REMINDERS),
        new Category("home", "Home",
            "Doorbell, motion while away, doors, the garage, laundry and leaks.", NORMAL, GROUP_HOME),
        new Category("reports", "Reports",
            "When a scheduled report is ready.", QUIET, GROUP_HOME),
        new Category("jobs", "Finished jobs",
            "Songs, pictures and audiobooks that are ready.", QUIET, GROUP_HOME),
        new Category("raven", "Raven missions",
            "Missions that finished or need your approval.", NORMAL, GROUP_HOME),
        new Category("app_updates", "App updates",
            "A new version of the Jarvis app is ready to install.", QUIET, GROUP_HOME),
        new Category("admin", "System alerts",
            "Services down or back, error spikes, deploys, backups and disk space.", NORMAL, GROUP_HOME),
        new Category(GENERAL, "Other notices",
            "Anything from Jarvis that has no category of its own.", NORMAL, GROUP_HOME),
        new Category(URGENT, "Urgent alerts",
            "SOS from family and emergencies such as a water leak.", POP_UP, null)
    ));

    /** Channel ids of earlier builds, replaced by the per-category channels. */
    static final List<String> RETIRED_CHANNELS = Collections.unmodifiableList(
        Arrays.asList("family_chat", "family_notices"));

    private PushCategories() {}

    /** The category with this id; unknown ids fall back to "Other notices". */
    static Category forId(String id) {
        Category general = null;
        for (Category c : ALL) {
            if (c.id.equals(id)) return c;
            if (GENERAL.equals(c.id)) general = c;
        }
        return general;
    }

    /** Urgent notices (priority 5: SOS, leaks) share one channel that always pops up. */
    static String channelId(String category, int priority) {
        if (PushNotice.clampPriority(priority) >= 5) return CHANNEL_PREFIX + URGENT;
        return forId(category).channelId();
    }

    /** ntfy's 1..5 onto NotificationCompat's PRIORITY_MIN (-2) .. PRIORITY_MAX (2). */
    static int compatPriority(int priority) {
        return PushNotice.clampPriority(priority) - 3;
    }

    /** Priority 1 and 2 arrive without sound or vibration whatever the channel. */
    static boolean silent(int priority) {
        return PushNotice.clampPriority(priority) <= 2;
    }
}
