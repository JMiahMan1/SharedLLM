package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.junit.Test;

public class ChatMessageLogTest {
    private static final long T = 1_760_000_000_000L;

    @Test
    public void aNotificationMatchesItsStoredTextEvenCutShort() {
        assertTrue(ChatMessageLog.sameMessage("See you at 5", "see you  at 5"));
        assertTrue(ChatMessageLog.sameMessage("Running late, the traffic on the 60 is…", "Running late, the traffic on the 60 is terrible today"));
        assertFalse(ChatMessageLog.sameMessage("See you at 6", "See you at 5"));
        assertFalse(ChatMessageLog.sameMessage("", "anything"));
    }

    @Test
    public void aTextInTheStoreIsNotPostedAgainButAChatMessageIs() {
        List<ChatMessageLog.Pending> pending = Arrays.asList(
                new ChatMessageLog.Pending("Mom", "Dinner at 6", T),          // also an SMS
                new ChatMessageLog.Pending("Leo", "omw", T + 1000),          // chat only
                new ChatMessageLog.Pending("Taylor", "k", T + 170_000));     // too new to tell
        List<ChatMessageLog.Stored> stored = Arrays.asList(new ChatMessageLog.Stored("Dinner at 6", T + 2000));
        List<ChatMessageLog.Pending> keep = new ArrayList<>();
        List<ChatMessageLog.Pending> post = ChatMessageLog.settle(pending, stored, T + ChatMessageLog.SETTLE_MS + 5000, keep);
        assertEquals(1, post.size());
        assertEquals("Leo", post.get(0).sender);
        assertEquals(1, keep.size());
        assertEquals("Taylor", keep.get(0).sender);
    }

    @Test
    public void aStoredTextFarFromTheNotificationIsADifferentMessage() {
        List<ChatMessageLog.Pending> pending = Arrays.asList(new ChatMessageLog.Pending("Mom", "ok", T));
        List<ChatMessageLog.Stored> stored = Arrays.asList(new ChatMessageLog.Stored("ok", T - 60 * 60 * 1000L));
        List<ChatMessageLog.Pending> post = ChatMessageLog.settle(pending, stored, T + ChatMessageLog.SETTLE_MS, new ArrayList<>());
        assertEquals(1, post.size());
    }
}
