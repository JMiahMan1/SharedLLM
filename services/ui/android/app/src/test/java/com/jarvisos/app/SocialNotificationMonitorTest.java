package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.HashMap;
import java.util.Map;

import org.junit.Test;

public class SocialNotificationMonitorTest {

    @Test
    public void extractsSocialMessagesOnProtectedPhoneAndDeduplicatesWithinWindow() {
        Map<String, Long> recent = new HashMap<>();
        SocialNotificationMonitor.Record first = SocialNotificationMonitor.extract(
                true, "severe", "com.snapchat.android", false, false, "msg",
                "Alex", "hey are you coming?", recent, 1_000L);
        assertNotNull(first);
        assertEquals("com.snapchat.android", first.pkg);
        assertEquals("Alex", first.sender);
        assertEquals("hey are you coming?", first.body);

        // Same message within 10 minutes is deduplicated.
        assertNull(SocialNotificationMonitor.extract(
                true, "severe", "com.snapchat.android", false, false, "msg",
                "Alex", "hey are you coming?", recent, 5 * 60_000L));

        // After 10 minutes it can be recorded again.
        assertNotNull(SocialNotificationMonitor.extract(
                true, "severe", "com.snapchat.android", false, false, "msg",
                "Alex", "hey are you coming?", recent, 11 * 60_000L));
    }

    @Test
    public void ignoresAdultPhonesMonitoringOffOngoingGroupSummaryAndSystemOrSmsPackages() {
        Map<String, Long> recent = new HashMap<>();
        assertNull(SocialNotificationMonitor.extract(
                false, "severe", "com.discord", false, false, "msg", "Sam", "hi", recent, 1_000L));
        assertNull(SocialNotificationMonitor.extract(
                true, "off", "com.discord", false, false, "msg", "Sam", "hi", recent, 1_000L));
        assertNull(SocialNotificationMonitor.extract(
                true, "severe", "com.discord", true, false, "msg", "Sam", "hi", recent, 1_000L));
        assertNull(SocialNotificationMonitor.extract(
                true, "severe", "com.discord", false, true, "msg", "Sam", "hi", recent, 1_000L));
        assertNull(SocialNotificationMonitor.extract(
                true, "severe", "com.spotify.music", false, false, "transport", "Song", "Artist", recent, 1_000L));
        assertNull(SocialNotificationMonitor.extract(
                true, "severe", "com.google.android.apps.messaging", false, false, "msg", "Mom", "dinner", recent, 1_000L));
        assertNull(SocialNotificationMonitor.extract(
                true, "severe", "com.jarvisos.app", false, false, "msg", "Jarvis", "notice", recent, 1_000L));
    }

    @Test
    public void onlyMessagesCountAsSocial() {
        // A known messaging app, a message category, or a conversation-style notification.
        assertTrue(SocialNotificationMonitor.isSocial("com.snapchat.android", null, false));
        assertTrue(SocialNotificationMonitor.isSocial("com.example.chat", "msg", false));
        assertTrue(SocialNotificationMonitor.isSocial("com.example.chat", null, true));
        // A game's or a shop's notifications are not social updates.
        assertFalse(SocialNotificationMonitor.isSocial("com.supercell.clashofclans", "promo", false));
        assertFalse(SocialNotificationMonitor.isSocial("com.amazon.mShop.android.shopping", null, false));
        assertFalse(SocialNotificationMonitor.isSocial(null, null, false));
    }
}
