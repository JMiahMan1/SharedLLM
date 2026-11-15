package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.junit.Test;

public class PrivateEnvelopeTest {
    private static final byte[] KEY = new byte[32];
    static {
        for (int i = 0; i < 32; i++) KEY[i] = (byte) i;
    }

    private static String seal(byte[] key, String json) throws Exception {
        byte[] iv = new byte[12];
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, iv));
        byte[] ct = c.doFinal(json.getBytes(StandardCharsets.UTF_8));
        String env = "{\"kind\":\"sealed\",\"v\":1,\"iv\":\"" + Base64.getEncoder().encodeToString(iv)
            + "\",\"ct\":\"" + Base64.getEncoder().encodeToString(ct) + "\"}";
        return "x\n\n```jarvis-envelope\n" + env + "\n```jarvis-envelope";
    }

    @Test
    public void opensTextPhotoAndComment() throws Exception {
        assertEquals("Dinner at 7?", PrivateEnvelope.previewText(seal(KEY, "{\"t\":\"text\",\"text\":\"Dinner at 7?\"}"), KEY));
        assertEquals("Photo", PrivateEnvelope.previewText(seal(KEY, "{\"t\":\"image\",\"blob\":\"ab\",\"mime\":\"image/png\"}"), KEY));
        assertEquals("On a photo: Lovely", PrivateEnvelope.previewText(seal(KEY, "{\"t\":\"comment\",\"blob\":\"ab\",\"text\":\"Lovely\"}"), KEY));
    }

    @Test
    public void aWrongKeyOrNoEnvelopeIsNull() throws Exception {
        byte[] other = new byte[32];
        assertNull(PrivateEnvelope.previewText(seal(KEY, "{\"t\":\"text\",\"text\":\"hi\"}"), other));
        assertNull(PrivateEnvelope.previewText("plain text", KEY));
        assertNull(PrivateEnvelope.previewText(seal(KEY, "{\"t\":\"poll\"}"), KEY));
    }

    @Test
    public void findsTheMessageById() {
        String json = "{\"status\":\"SUCCESS\",\"detail\":{\"messages\":[{\"id\":3,\"message\":\"a\"},{\"id\":4,\"message\":\"b\"}]}}";
        assertEquals("b", PrivateEnvelope.findMessage(json, 4));
        assertNull(PrivateEnvelope.findMessage(json, 9));
        assertNull(PrivateEnvelope.findMessage("not json", 4));
    }

    @Test
    public void aPrivateNoticeCarriesItsMessageIdAndAPreviewOnlyWhenAdded() {
        PushNotice n = PushNotice.parse("{\"title\":\"Private chat\",\"body\":\"New private message from Sam\",\"category\":\"chat\","
            + "\"data\":{\"token\":\"room123abc\",\"message_id\":42,\"private\":true}}");
        org.junit.Assert.assertTrue(n.privateChat);
        assertEquals(42L, n.messageId);
        org.junit.Assert.assertFalse(n.preview);
        assertEquals("New private message from Sam", n.body);
        PushNotice shown = n.withPreview("Dinner at 7?");
        org.junit.Assert.assertTrue(shown.preview && shown.privateChat);
        assertEquals("Dinner at 7?", shown.body);
        assertEquals("room123abc", shown.chatToken());
        PushNotice other = PushNotice.parse("{\"title\":\"Chat\",\"body\":\"hi\",\"category\":\"chat\",\"data\":{\"token\":\"fam1\"}}");
        org.junit.Assert.assertFalse(other.privateChat);
        assertEquals(0L, other.messageId);
    }
}
