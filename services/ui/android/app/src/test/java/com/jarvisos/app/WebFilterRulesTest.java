package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import com.jarvisos.app.WebFilterRules.Decision;
import com.jarvisos.app.WebFilterRules.Kind;

import java.util.Arrays;
import java.util.List;

import org.junit.Test;

public class WebFilterRulesTest {

    private static final List<String> BLOCK = Arrays.asList("tiktok.com", "reddit.com");
    private static final List<String> ALLOW = Arrays.asList("khanacademy.org", "help.reddit.com");

    private static Decision d(String name) {
        return WebFilterRules.decide(name, true, true, BLOCK, ALLOW);
    }

    @Test
    public void aBlockedDomainAndEveryNameUnderItAreBlocked() {
        assertEquals(Kind.BLOCK, d("tiktok.com").kind);
        assertEquals(Kind.BLOCK, d("WWW.TikTok.com.").kind);
        assertEquals(Kind.FORWARD, d("nottiktok.com").kind);
    }

    @Test
    public void theAllowListWinsAndUsesTheOpenResolver() {
        Decision help = d("help.reddit.com");
        assertEquals(Kind.FORWARD, help.kind);
        assertEquals(WebFilterRules.OPEN_RESOLVER, help.target);
        assertEquals(WebFilterRules.OPEN_RESOLVER, d("www.khanacademy.org").target);
    }

    @Test
    public void everythingElseGoesToTheFamilySafeResolver() {
        assertEquals(WebFilterRules.FAMILY_RESOLVER, d("example.com").target);
        assertEquals(WebFilterRules.OPEN_RESOLVER,
                WebFilterRules.decide("example.com", false, false, BLOCK, ALLOW).target);
    }

    @Test
    public void searchEnginesAnswerWithTheirSafeSearchAddress() {
        assertEquals("forcesafesearch.google.com", d("www.google.com").target);
        assertEquals("forcesafesearch.google.com", d("google.co.uk").target);
        assertEquals("restrict.youtube.com", d("m.youtube.com").target);
        assertEquals("strict.bing.com", d("www.bing.com").target);
        assertEquals(Kind.FORWARD, d("forcesafesearch.google.com").kind); // no loop
        assertEquals(Kind.FORWARD, d("mail.google.com").kind);
        assertNull(WebFilterRules.safeHost("googleusercontent.com"));
    }

    @Test
    public void safeSearchOffLeavesSearchAlone() {
        assertEquals(Kind.FORWARD, WebFilterRules.decide("www.google.com", true, false, BLOCK, ALLOW).kind);
    }
}
