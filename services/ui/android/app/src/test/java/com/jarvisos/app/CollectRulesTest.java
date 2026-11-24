package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

import org.junit.Test;

public class CollectRulesTest {

    private static final Set<String> CHAT = new HashSet<>(Collections.singletonList("com.example.chat"));

    @Test
    public void theDefaultSmsAppDialerAndChatAppsCount() {
        assertTrue(CollectRules.isCommsSource("com.example.sms", "com.example.sms", "com.example.dialer", CHAT));
        assertTrue(CollectRules.isCommsSource("com.example.dialer", "com.example.sms", "com.example.dialer", CHAT));
        assertTrue(CollectRules.isCommsSource("com.example.chat", null, null, CHAT));
    }

    @Test
    public void otherAppsAndNothingDoNot() {
        assertFalse(CollectRules.isCommsSource("com.example.game", "com.example.sms", "com.example.dialer", CHAT));
        assertFalse(CollectRules.isCommsSource(null, "com.example.sms", "com.example.dialer", CHAT));
        assertFalse(CollectRules.isCommsSource("", "", "", CHAT));
        assertFalse(CollectRules.isCommsSource("com.example.chat", null, null, null));
    }

    @Test
    public void aLiveEntryWinsOverACancelledOne() {
        assertEquals("ENQUEUED", CollectRules.summarize(Arrays.asList("CANCELLED", "ENQUEUED")));
        assertEquals("CANCELLED", CollectRules.summarize(Collections.singletonList("CANCELLED")));
        assertEquals("NONE", CollectRules.summarize(Collections.emptyList()));
        assertEquals("NONE", CollectRules.summarize(null));
        assertTrue(CollectRules.isScheduled("RUNNING"));
        assertTrue(CollectRules.isScheduled("BLOCKED"));
        assertFalse(CollectRules.isScheduled("CANCELLED"));
        assertFalse(CollectRules.isScheduled("NONE"));
    }

    @Test
    public void theGateAllowsOneRunAtATime() {
        CollectRules.Gate gate = new CollectRules.Gate(30_000L);
        assertTrue(gate.tryStart(1_000L));
        assertFalse("one in flight", gate.tryStart(100_000L));
        gate.finish();
        assertFalse(gate.isRunning());
    }

    @Test
    public void theGateWaitsItsGapAfterAStart() {
        CollectRules.Gate gate = new CollectRules.Gate(30_000L);
        assertTrue(gate.tryStart(1_000L));
        gate.finish();
        assertFalse("too soon", gate.tryStart(30_999L));
        assertTrue(gate.tryStart(31_000L));
        gate.finish();
    }

    @Test
    public void aClockThatWentBackDoesNotLockTheGateForever() {
        CollectRules.Gate gate = new CollectRules.Gate(30_000L);
        assertTrue(gate.tryStart(1_000_000L));
        gate.finish();
        assertTrue(gate.tryStart(5_000L));
    }
}
