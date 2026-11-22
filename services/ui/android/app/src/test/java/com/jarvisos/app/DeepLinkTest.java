package com.jarvisos.app;

import static org.junit.Assert.assertEquals;

import org.json.JSONObject;
import org.junit.Test;

/**
 * Where a tapped notice opens (docs/PUSH.md, "Deep links"): the structured
 * link a notice carries, the older url, and the intent a notification tap
 * delivers, whether the app was running, started cold or opened after unlock.
 */
public class DeepLinkTest {

    private static JSONObject json(String s) throws Exception {
        return new JSONObject(s);
    }

    @Test
    public void buildsThePageAndTheItemFromALink() throws Exception {
        JSONObject link = json("{\"route\":\"member\",\"path\":\"/family/member/sam\","
            + "\"query\":{\"section\":\"time\"},\"hash\":\"\",\"focus\":\"unlock-21\",\"guard\":\"parent\"}");
        assertEquals("/family/member/sam?section=time&focus=unlock-21", DeepLink.fromLink(link));
    }

    @Test
    public void queryKeysAreSortedAndEncoded() throws Exception {
        JSONObject link = json("{\"route\":\"bible\",\"path\":\"/bible\","
            + "\"query\":{\"verse\":\"16\",\"ref\":\"John 3\"},\"hash\":\"v16\"}");
        assertEquals("/bible?ref=John%203&verse=16#v16", DeepLink.fromLink(link));
    }

    @Test
    public void aSectionAnchorFollowsTheQuery() throws Exception {
        JSONObject link = json("{\"route\":\"devices\",\"path\":\"/devices\",\"query\":{},"
            + "\"hash\":\"add\",\"focus\":\"discovery-ab12\"}");
        assertEquals("/devices?focus=discovery-ab12#add", DeepLink.fromLink(link));
    }

    @Test
    public void refusesAddressesOutsideTheApp() throws Exception {
        assertEquals("", DeepLink.fromLink(json("{\"route\":\"x\",\"path\":\"https://evil.example/\"}")));
        assertEquals("", DeepLink.fromLink(json("{\"route\":\"x\",\"path\":\"//evil.example/x\"}")));
        assertEquals("", DeepLink.fromLink(json("{\"route\":\"x\",\"path\":\"/a?b=c\"}")));
        assertEquals("", DeepLink.fromLink(null));
        // A hash that is not a plain anchor is dropped, the page is kept.
        assertEquals("/devices", DeepLink.fromLink(json("{\"route\":\"devices\",\"path\":\"/devices\",\"hash\":\"<x>\"}")));
    }

    @Test
    public void prefersTheLinkThenTheUrl() throws Exception {
        assertEquals("/garden?focus=plant-4", DeepLink.forNotice(json(
            "{\"link\":{\"route\":\"garden\",\"path\":\"/garden\",\"focus\":\"plant-4\"},\"url\":\"/garden\"}")));
        assertEquals("/wander?person=leo", DeepLink.forNotice(json("{\"url\":\"/wander?person=leo\"}")));
        assertEquals("", DeepLink.forNotice(json("{\"url\":\"https://example.com\"}")));
        assertEquals("", DeepLink.forNotice(null));
    }

    @Test
    public void aNoticeCarriesItsLinkAsItsPath() {
        PushNotice n = PushNotice.parse("{\"id\":\"n1\",\"category\":\"family_safety\",\"title\":\"Unlock?\","
            + "\"data\":{\"link\":{\"route\":\"member\",\"path\":\"/family/member/sam\",\"query\":{},"
            + "\"focus\":\"unlock-21\"},\"url\":\"/family/member/sam?focus=unlock-21\"}}");
        assertEquals("/family/member/sam?focus=unlock-21", n.path);
    }

    @Test
    public void readsTheTappedIntent() {
        // Running app or cold start: the extra the notification set.
        assertEquals("/family?chat=t&focus=msg-9", DeepLink.fromIntent("/family?chat=t&focus=msg-9", null));
        // The extra lost (re-delivered intent): the jarvis://open data URI still says where.
        assertEquals("/admin/ops?mission=7", DeepLink.fromIntent(null, "jarvis://open/admin/ops?mission=7"));
        assertEquals("/", DeepLink.fromIntent("", "jarvis://open/"));
        // Nothing else opens a page: another scheme, the garage confirm, a full URL.
        assertEquals("", DeepLink.fromIntent(null, "jarvis://confirm-garage"));
        assertEquals("", DeepLink.fromIntent(null, "https://evil.example/x"));
        assertEquals("", DeepLink.fromIntent("https://evil.example/x", "jarvis://openhttps://x"));
        assertEquals("", DeepLink.fromIntent(null, null));
    }
}
