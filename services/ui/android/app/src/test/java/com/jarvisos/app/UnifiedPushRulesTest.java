package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Map;
import org.json.JSONObject;
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

    private static final String LIST = "{\"status\":\"SUCCESS\",\"registrations\":["
        + "{\"device_id\":\"d1\",\"app\":\"org.tusky\",\"token\":\"t1\",\"topic\":\"upAAA\","
        + "\"endpoint\":\"https://jarvis.example/push/upAAA\",\"this_device\":true},"
        + "{\"device_id\":\"d2\",\"app\":\"org.tusky\",\"token\":\"t2\",\"topic\":\"upBBB\","
        + "\"endpoint\":\"https://jarvis.example/push/upBBB\",\"this_device\":false},"
        + "{\"device_id\":\"d1\",\"app\":\"at.bitfire.davdroid\",\"token\":\"t3\",\"topic\":\"upCCC\","
        + "\"endpoint\":\"https://jarvis.example/push/upCCC\",\"this_device\":true}]}";

    @Test
    public void theServerListIsReadForThisDeviceOnly() throws Exception {
        Map<String, UnifiedPushRegistration> mine = UnifiedPushRegistration.thisDevice(new JSONObject(LIST));
        assertEquals(2, mine.size());
        assertEquals("upAAA", mine.get(UnifiedPushRegistration.key("org.tusky", "t1")).topic);
        assertNull(mine.get(UnifiedPushRegistration.key("org.tusky", "t2")));
        assertEquals("upCCC", mine.get(UnifiedPushRegistration.key("at.bitfire.davdroid", "t3")).topic);
    }

    @Test
    public void anAnswerWithoutAListRemovesNothing() throws Exception {
        assertNull(UnifiedPushRegistration.thisDevice(new JSONObject("{\"detail\":\"oops\"}")));
        assertNull(UnifiedPushRegistration.thisDevice(null));
        assertTrue(UnifiedPushRegistration.thisDevice(new JSONObject("{\"registrations\":[]}")).isEmpty());
    }

    @Test
    public void aRegistrationIsGoneMovedOrUnchanged() throws Exception {
        Map<String, UnifiedPushRegistration> server = UnifiedPushRegistration.thisDevice(new JSONObject(LIST));
        UnifiedPushRegistration same = new UnifiedPushRegistration(
            "t1", "org.tusky", "upAAA", "https://jarvis.example/push/upAAA", "Mastodon");
        UnifiedPushRegistration moved = new UnifiedPushRegistration(
            "t3", "at.bitfire.davdroid", "upOLD", "https://jarvis.example/push/upOLD", null);
        UnifiedPushRegistration removedElsewhere = new UnifiedPushRegistration(
            "t9", "org.tusky", "upZZZ", "https://jarvis.example/push/upZZZ", null);

        assertTrue(same.sameDelivery(server.get(UnifiedPushRegistration.key(same.app, same.token))));
        UnifiedPushRegistration there = server.get(UnifiedPushRegistration.key(moved.app, moved.token));
        assertFalse(moved.sameDelivery(there));
        UnifiedPushRegistration now = moved.movedTo(there);
        assertEquals("upCCC", now.topic);
        assertEquals("https://jarvis.example/push/upCCC", now.endpoint);
        assertEquals("t3", now.token);
        assertNull(server.get(UnifiedPushRegistration.key(removedElsewhere.app, removedElsewhere.token)));
    }

    @Test
    public void aTokenOfAnotherAppIsNotTheSameRegistration() throws Exception {
        Map<String, UnifiedPushRegistration> server = UnifiedPushRegistration.thisDevice(new JSONObject(LIST));
        assertNull(server.get(UnifiedPushRegistration.key("com.evil", "t1")));
    }
}
