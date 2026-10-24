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

    @Test
    public void aSiteAllowedForAWhileCountsUntilItsTimeRunsOut() {
        java.util.Map<String, Long> until = new java.util.HashMap<>();
        until.put("minecraft.net", 5_000L);
        until.put("old.example", 1_000L);
        List<String> now = WebFilterRules.activeAllow(ALLOW, until, 2_000L);
        assertEquals(Arrays.asList("khanacademy.org", "help.reddit.com", "minecraft.net"), now);
        assertEquals(ALLOW, WebFilterRules.activeAllow(ALLOW, until, 6_000L));
        // An allowed-for-now site wins over the block list, like the parent's own allow list.
        assertEquals(Kind.FORWARD, WebFilterRules.decide("www.tiktok.com", true, true, BLOCK,
                WebFilterRules.activeAllow(ALLOW, java.util.Collections.singletonMap("tiktok.com", 9_000L), 2_000L)).kind);
    }

    @Test
    public void aBlockedNameIsAskedAboutAsItsSite() {
        assertEquals("tiktok.com", WebFilterRules.site("WWW.TikTok.com."));
        assertEquals("m.tiktok.com", WebFilterRules.site("m.tiktok.com"));
    }

    @Test
    public void theFamilyResolversZeroAnswerMeansBlocked() {
        assertEquals(true, WebFilterRules.familyBlocked(Arrays.asList(new byte[] {0, 0, 0, 0})));
        assertEquals(false, WebFilterRules.familyBlocked(Arrays.asList(new byte[] {0, 0, 0, 0}, new byte[] {1, 2, 3, 4})));
        assertEquals(false, WebFilterRules.familyBlocked(java.util.Collections.emptyList()));
    }

    @Test
    public void askAParentIsOfferedOncePerSiteEveryHalfHour() {
        java.util.Map<String, Long> offered = new java.util.HashMap<>();
        assertEquals(true, WebFilterRules.shouldOffer(offered, "tiktok.com", 0));
        assertEquals(false, WebFilterRules.shouldOffer(offered, "tiktok.com", 29 * 60_000L));
        assertEquals(true, WebFilterRules.shouldOffer(offered, "reddit.com", 29 * 60_000L));
        assertEquals(true, WebFilterRules.shouldOffer(offered, "tiktok.com", 31 * 60_000L));
    }
}
