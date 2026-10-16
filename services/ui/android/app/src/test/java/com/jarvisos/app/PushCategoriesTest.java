package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.Test;

/**
 * Category to Android channel. Every category in docs/PUSH.md needs its own
 * channel so it can be silenced in Android's settings without silencing the
 * rest, and urgent notices must reach a channel that pops up.
 */
public class PushCategoriesTest {

    private static final List<String> CONTRACT = Arrays.asList(
        "chat", "family_presence", "family_safety", "leave_by", "calendar", "timers", "bible",
        "reports", "home", "jobs", "raven", "app_updates", "health", "admin");

    @Test
    public void everyContractCategoryHasItsOwnChannel() {
        Set<String> channels = new HashSet<>();
        for (String category : CONTRACT) {
            PushCategories.Category c = PushCategories.forId(category);
            assertEquals(category, c.id);
            assertTrue(channels.add(PushCategories.channelId(category, 3)));
        }
    }

    @Test
    public void channelIdsAreNewSoOldChannelsCanBeDeleted() {
        for (PushCategories.Category c : PushCategories.ALL) {
            assertTrue(c.channelId().startsWith(PushCategories.CHANNEL_PREFIX));
            assertFalse(PushCategories.RETIRED_CHANNELS.contains(c.channelId()));
            // BackgroundNotice.CHANNEL_ID, the hidden service notice, stays as it is.
            assertFalse("background_hidden".equals(c.channelId()));
        }
    }

    @Test
    public void unknownCategoriesUseOtherNotices() {
        assertEquals("jarvis_general", PushCategories.channelId("something_new", 3));
        assertEquals("jarvis_general", PushCategories.channelId(null, 3));
    }

    @Test
    public void urgentNoticesUseTheUrgentChannelWhateverTheCategory() {
        assertEquals("jarvis_urgent", PushCategories.channelId("family_safety", 5));
        assertEquals("jarvis_urgent", PushCategories.channelId("home", 5));
        assertEquals("jarvis_home", PushCategories.channelId("home", 4));
        assertEquals(PushCategories.POP_UP, PushCategories.forId(PushCategories.URGENT).importance);
    }

    @Test
    public void priorityMapsOntoNotificationCompat() {
        assertEquals(-2, PushCategories.compatPriority(1));
        assertEquals(0, PushCategories.compatPriority(3));
        assertEquals(2, PushCategories.compatPriority(5));
        assertEquals(2, PushCategories.compatPriority(42));
    }

    @Test
    public void lowPriorityArrivesSilently() {
        assertTrue(PushCategories.silent(1));
        assertTrue(PushCategories.silent(2));
        assertFalse(PushCategories.silent(3));
        assertFalse(PushCategories.silent(5));
    }

    @Test
    public void everyChannelHasANameAndDescriptionPeopleCanRead() {
        for (PushCategories.Category c : PushCategories.ALL) {
            assertFalse(c.name.isEmpty());
            assertFalse(c.description.isEmpty());
            assertFalse(c.name.contains("_"));
        }
    }

    @Test
    public void onlyJarvisNoticeChannelsAreClearedOnAnAccountSwitch() {
        for (PushCategories.Category c : PushCategories.ALL) {
            assertTrue(PushCategories.isPushChannel(c.channelId()));
        }
        assertTrue(PushCategories.isPushChannel(PushCategories.channelId("anything", 5)));
        // BackgroundNotice's and MediaPlaybackService's channels stay.
        assertFalse(PushCategories.isPushChannel("background_hidden"));
        assertFalse(PushCategories.isPushChannel("media_playback"));
        assertFalse(PushCategories.isPushChannel(null));
    }
}
