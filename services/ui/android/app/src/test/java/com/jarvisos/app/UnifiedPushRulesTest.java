package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import org.junit.Test;

/**
 * What the UnifiedPush AND_3 spec asks a distributor to check before acting
 * on a request from another app, and the registration record kept per token.
 */
public class UnifiedPushRulesTest {

    private static String repeat(char c, int n) {
        char[] chars = new char[n];
        Arrays.fill(chars, c);
        return new String(chars);
    }

    @Test
    public void tokensAreRequiredAndAtMostOneHundredBytes() {
        assertTrue(UnifiedPushRules.validToken("3f1c2a5e-7c1b-4e0f-9b1d-2c3d4e5f6a7b"));
        assertTrue(UnifiedPushRules.validToken(repeat('a', 100)));
        assertFalse(UnifiedPushRules.validToken(repeat('a', 101)));
        // 34 three-byte characters are 102 bytes although only 34 chars.
        assertFalse(UnifiedPushRules.validToken(repeat('€', 34)));
        assertFalse(UnifiedPushRules.validToken(""));
        assertFalse(UnifiedPushRules.validToken(null));
    }

    @Test
    public void vapidIsOptionalButMustBeAnUncompressedP256KeyWhenSent() {
        assertTrue(UnifiedPushRules.validVapid(null));
        assertTrue(UnifiedPushRules.validVapid("B" + repeat('x', 86)));
        assertFalse(UnifiedPushRules.validVapid(repeat('x', 86)));
        assertFalse(UnifiedPushRules.validVapid(repeat('x', 86) + "="));
    }

    @Test
    public void anOverlongDescriptionIsDroppedNotFatal() {
        assertEquals("Talk", UnifiedPushRules.description("Talk"));
        assertNull(UnifiedPushRules.description(repeat('d', 101)));
        assertNull(UnifiedPushRules.description(""));
    }

    @Test
    public void sharedIdentityIsPreferredThenThePendingIntentCreator() {
        assertEquals("com.nextcloud.talk2", UnifiedPushRules.callerPackage("com.nextcloud.talk2", "other"));
        assertEquals("at.bitfire.davdroid", UnifiedPushRules.callerPackage(null, "at.bitfire.davdroid"));
        assertEquals("at.bitfire.davdroid", UnifiedPushRules.callerPackage("", "at.bitfire.davdroid"));
        assertNull(UnifiedPushRules.callerPackage(null, null));
    }

    @Test
    public void onlyTheOwningAppOrAnUnknownCallerMayUnregister() {
        assertTrue(UnifiedPushRules.mayUnregister("com.a", "com.a"));
        assertTrue(UnifiedPushRules.mayUnregister("com.a", null));
        assertFalse(UnifiedPushRules.mayUnregister("com.a", "com.b"));
    }

    @Test
    public void upTopicsAreRecognised() {
        assertTrue(UnifiedPushRules.isUpTopic("upAbCdEfGhIjKlMnOpQrStUvWxYz012345"));
        assertTrue(UnifiedPushRules.isUpTopic("up_x-1"));
        assertFalse(UnifiedPushRules.isUpTopic("up"));
        assertFalse(UnifiedPushRules.isUpTopic("jarvis-michele-secret"));
        assertFalse(UnifiedPushRules.isUpTopic("up/../admin"));
        assertFalse(UnifiedPushRules.isUpTopic(""));
        assertFalse(UnifiedPushRules.isUpTopic(null));
    }

    @Test
    public void messagesAreOneTo4096Bytes() {
        assertTrue(UnifiedPushRules.validMessage(new byte[1]));
        assertTrue(UnifiedPushRules.validMessage(new byte[4096]));
        assertFalse(UnifiedPushRules.validMessage(new byte[0]));
        assertFalse(UnifiedPushRules.validMessage(new byte[4097]));
        assertFalse(UnifiedPushRules.validMessage(null));
    }

    @Test
    public void endpointsMustBeHttpsAndShort() {
        assertTrue(UnifiedPushRules.validEndpoint("https://jarvis.sumemail.com/push/upabc"));
        assertFalse(UnifiedPushRules.validEndpoint("http://jarvis.sumemail.com/push/upabc"));
        assertFalse(UnifiedPushRules.validEndpoint("https://" + repeat('a', 1000)));
        assertFalse(UnifiedPushRules.validEndpoint(null));
    }

    @Test
    public void aRegistrationSurvivesStorage() {
        UnifiedPushRegistration r = new UnifiedPushRegistration(
            "tok-1", "com.nextcloud.talk2", "upabc123", "https://jarvis.sumemail.com/push/upabc123", "Talk");
        UnifiedPushRegistration back = UnifiedPushRegistration.fromJson(r.toJson());
        assertNotNull(back);
        assertEquals("tok-1", back.token);
        assertEquals("com.nextcloud.talk2", back.app);
        assertEquals("upabc123", back.topic);
        assertEquals("https://jarvis.sumemail.com/push/upabc123", back.endpoint);
        assertEquals("Talk", back.description);
    }

    @Test
    public void aDamagedRecordIsNotUsed() {
        assertNull(UnifiedPushRegistration.fromJson(null));
        assertNull(UnifiedPushRegistration.fromJson("garbage"));
        assertNull(UnifiedPushRegistration.fromJson("{\"token\":\"t\",\"app\":\"a\",\"topic\":\"nope\",\"endpoint\":\"e\"}"));
        UnifiedPushRegistration noDescription = UnifiedPushRegistration.fromJson(
            "{\"token\":\"t\",\"app\":\"a\",\"topic\":\"upx\",\"endpoint\":\"https://e\"}");
        assertNotNull(noDescription);
        assertNull(noDescription.description);
    }
}
