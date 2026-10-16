package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * The Jarvis notice inside a stream message (docs/PUSH.md): which
 * conversation a chat belongs to, which notification a tag replaces, which
 * pages may be opened and which action buttons are usable.
 */
public class PushNoticeTest {

    private static final String CHAT = "{\"id\":\"n1\",\"category\":\"chat\",\"kind\":\"chat\","
        + "\"title\":\"Jeremiah\",\"body\":\"On my way\",\"priority\":4,\"tag\":\"chat:abc123\","
        + "\"data\":{\"url\":\"/family?chat=abc123\"},"
        + "\"actions\":["
        + "{\"label\":\"Reply\",\"type\":\"reply\",\"callback\":\"chat_reply\",\"params\":{\"token\":\"abc123\"}},"
        + "{\"label\":\"Open\",\"type\":\"view\",\"url\":\"/family?chat=abc123\"}]}";

    @Test
    public void readsTheContractExample() {
        PushNotice n = PushNotice.parse(CHAT);
        assertEquals("chat", n.category);
        assertEquals("Jeremiah", n.title);
        assertEquals("On my way", n.body);
        assertEquals(4, n.priority);
        assertEquals("/family?chat=abc123", n.path);
        assertEquals(2, n.actions.size());
        assertTrue(n.actions.get(0).isReply());
        assertEquals("chat_reply", n.actions.get(0).callback);
        assertEquals("{\"token\":\"abc123\"}", n.actions.get(0).params);
        assertTrue(n.actions.get(1).isView());
    }

    @Test
    public void aChatBelongsToTheConversationInItsTag() {
        assertEquals("abc123", PushNotice.parse(CHAT).chatToken());
    }

    @Test
    public void dataTokenWinsOverTheTag() {
        PushNotice n = PushNotice.parse("{\"kind\":\"chat\",\"title\":\"M\",\"body\":\"hi\","
            + "\"tag\":\"chat:old\",\"data\":{\"token\":\"t9\"}}");
        assertEquals("t9", n.chatToken());
    }

    @Test
    public void aNonChatNoticeIsNotGroupedAsAConversation() {
        PushNotice n = PushNotice.parse("{\"category\":\"timers\",\"title\":\"Timer\",\"body\":\"Done\","
            + "\"tag\":\"chat:looks-like-one\"}");
        assertNull(n.chatToken());
        assertFalse(n.isChat());
    }

    @Test
    public void theSameTagReplacesTheSameNotification() {
        PushNotice first = PushNotice.parse("{\"id\":\"1\",\"category\":\"timers\",\"title\":\"Pasta\","
            + "\"body\":\"5 min left\",\"tag\":\"timer:pasta\"}");
        PushNotice second = PushNotice.parse("{\"id\":\"2\",\"category\":\"timers\",\"title\":\"Pasta\","
            + "\"body\":\"Done\",\"tag\":\"timer:pasta\"}");
        assertEquals(first.notificationId(), second.notificationId());
    }

    @Test
    public void untaggedNoticesEachGetTheirOwnNotification() {
        PushNotice a = PushNotice.parse("{\"id\":\"1\",\"title\":\"A\",\"body\":\"same\"}");
        PushNotice b = PushNotice.parse("{\"id\":\"2\",\"title\":\"A\",\"body\":\"same\"}");
        assertNotEquals(a.notificationId(), b.notificationId());
    }

    @Test
    public void missingCategoryIsGeneralAndPriorityIsClamped() {
        PushNotice n = PushNotice.parse("{\"title\":\"x\",\"body\":\"y\",\"priority\":9}");
        assertEquals(PushCategories.GENERAL, n.category);
        assertEquals(5, n.priority);
        assertEquals(1, PushNotice.parse("{\"title\":\"x\",\"priority\":0}").priority);
        assertEquals(3, PushNotice.parse("{\"title\":\"x\"}").priority);
    }

    @Test
    public void onlyPagesInsideTheAppCanBeOpened() {
        assertEquals("/family", PushNotice.safePath("/family"));
        assertEquals("", PushNotice.safePath("https://evil.example/"));
        assertEquals("", PushNotice.safePath("//evil.example/"));
        assertEquals("", PushNotice.safePath("/\\evil.example"));
        assertEquals("", PushNotice.safePath("javascript:alert(1)"));
        assertEquals("", PushNotice.safePath(null));
    }

    @Test
    public void unusableActionsAreDroppedAndAtMostThreeKept() {
        PushNotice n = PushNotice.parse("{\"title\":\"Raven\",\"body\":\"Approve?\",\"actions\":["
            + "{\"label\":\"Open\",\"type\":\"view\",\"url\":\"https://elsewhere\"},"
            + "{\"label\":\"\",\"type\":\"callback\",\"callback\":\"raven_approve\"},"
            + "{\"label\":\"Mystery\",\"type\":\"http\",\"url\":\"/x\"},"
            + "{\"label\":\"Approve\",\"type\":\"callback\",\"callback\":\"raven_approve\"},"
            + "{\"label\":\"Reject\",\"type\":\"callback\",\"callback\":\"raven_reject\"},"
            + "{\"label\":\"Open\",\"type\":\"view\",\"url\":\"/admin\"},"
            + "{\"label\":\"Fourth\",\"type\":\"view\",\"url\":\"/\"}]}");
        assertEquals(3, n.actions.size());
        assertEquals("Approve", n.actions.get(0).label);
        assertEquals("{}", n.actions.get(0).params);
        assertEquals("Reject", n.actions.get(1).label);
        assertEquals("/admin", n.actions.get(2).url);
    }

    @Test
    public void textThatIsNotANoticeIsRejected() {
        assertNull(PushNotice.parse("Backup failed on nas"));
        assertNull(PushNotice.parse("{}"));
        assertNull(PushNotice.parse(null));
    }

    @Test
    public void plainTextIsShownAsAnOtherNotice() {
        PushNotice n = PushNotice.plain("m1", "", "Backup failed on nas");
        assertEquals("Jarvis", n.title);
        assertEquals("Backup failed on nas", n.body);
        assertEquals(PushCategories.GENERAL, n.category);
        assertTrue(n.actions.isEmpty());
    }
}
