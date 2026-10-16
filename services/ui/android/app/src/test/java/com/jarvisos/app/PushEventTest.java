package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * Lines of the push stream (ntfy's JSON stream through the gateway).
 *
 * What these guard: a keepalive or a half-written line must never end the
 * stream or be shown as a notification, the message id must be read so the
 * next connection can ask for {@code since} it, and a message on an
 * {@code up*} topic must go to UnifiedPush rather than become a Jarvis notice.
 */
public class PushEventTest {

    @Test
    public void readsAMessageWithItsIdForTheNextSince() {
        PushEvent e = PushEvent.parse(
            "{\"id\":\"hwQ2YpKdmg\",\"time\":1760000000,\"event\":\"message\","
            + "\"message\":\"{\\\"title\\\":\\\"Jeremiah\\\",\\\"body\\\":\\\"On my way\\\"}\"}");
        assertNotNull(e);
        assertTrue(e.isMessage());
        assertEquals("hwQ2YpKdmg", e.id);
        assertEquals("{\"title\":\"Jeremiah\",\"body\":\"On my way\"}", e.message);
        assertFalse(e.isUnifiedPush());
    }

    @Test
    public void openAndKeepaliveShowTheLinkIsAliveButAreNotMessages() {
        PushEvent open = PushEvent.parse("{\"id\":\"a\",\"time\":1,\"event\":\"open\",\"topic\":\"\"}");
        PushEvent keepalive = PushEvent.parse("{\"id\":\"b\",\"time\":2,\"event\":\"keepalive\"}");
        assertTrue(open.isLiveness());
        assertFalse(open.isMessage());
        assertTrue(keepalive.isLiveness());
        assertFalse(keepalive.isMessage());
    }

    @Test
    public void aMessageOnAnUpTopicIsForUnifiedPush() {
        PushEvent e = PushEvent.parse(
            "{\"id\":\"x1\",\"event\":\"message\",\"topic\":\"upAbCdEfGhIjKlMnOpQrStUvWxYz012345\","
            + "\"message\":\"AAEC\",\"encoding\":\"base64\"}");
        assertTrue(e.isUnifiedPush());
        assertTrue(e.base64);
        assertEquals("AAEC", e.message);
    }

    @Test
    public void aPlainTextBodyIsNotBase64() {
        PushEvent e = PushEvent.parse("{\"id\":\"x2\",\"event\":\"message\",\"topic\":\"upabc\",\"message\":\"hi\"}");
        assertFalse(e.base64);
    }

    @Test
    public void blankBrokenAndEventlessLinesAreSkipped() {
        assertNull(PushEvent.parse(null));
        assertNull(PushEvent.parse(""));
        assertNull(PushEvent.parse("   "));
        assertNull(PushEvent.parse("{\"id\":\"cut off"));
        assertNull(PushEvent.parse("not json"));
        assertNull(PushEvent.parse("{\"id\":\"no-event\"}"));
    }

    @Test
    public void jsonNullFieldsReadAsEmpty() {
        PushEvent e = PushEvent.parse("{\"id\":null,\"event\":\"message\",\"topic\":null,\"message\":\"x\"}");
        assertEquals("", e.id);
        assertEquals("", e.topic);
        assertFalse(e.isUnifiedPush());
    }
}
