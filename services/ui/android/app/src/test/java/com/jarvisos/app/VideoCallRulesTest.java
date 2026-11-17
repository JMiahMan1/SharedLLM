package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNotSame;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;

import org.junit.Test;

public class VideoCallRulesTest {

    private static final String CHROME = "com.android.chrome";
    private static final String CHAT_APP = "com.example.videochat";

    private static VideoCallRules.Settings settings(boolean scan, boolean block, int maxFrames) {
        Map<String, List<String>> browsers = new HashMap<>();
        browsers.put(CHROME, Collections.singletonList(CHROME + ":id/url_bar"));
        return new VideoCallRules.Settings(scan, block, 45_000L, maxFrames,
                new HashSet<>(Collections.singletonList(CHAT_APP)),
                Arrays.asList("facetime.apple.com", "*.zoom.us"), browsers);
    }

    @Test
    public void theAddressBarGivesTheHost() {
        assertEquals("facetime.apple.com", VideoCallRules.host("facetime.apple.com/join#v=1&p=abc"));
        assertEquals("meet.google.com", VideoCallRules.host("https://Meet.Google.com/abc-defg-hij"));
        assertEquals("us02web.zoom.us", VideoCallRules.host("us02web.zoom.us:443/j/123"));
        assertEquals("", VideoCallRules.host("how to make a video call"));
        assertEquals("", VideoCallRules.host(null));
    }

    @Test
    public void aSiteCoversTheNamesUnderIt() {
        List<String> sites = Arrays.asList("facetime.apple.com", "*.zoom.us");
        assertEquals("zoom.us", VideoCallRules.matchSite("us02web.zoom.us", sites));
        assertEquals("facetime.apple.com", VideoCallRules.matchSite("facetime.apple.com", sites));
        assertNull(VideoCallRules.matchSite("apple.com", sites));
        assertNull(VideoCallRules.matchSite("notzoom.us", sites));
    }

    @Test
    public void aListedAppOrAListedSiteInABrowserIsACallSource() {
        VideoCallRules.Settings s = settings(true, false, 20);
        assertEquals(CHAT_APP, VideoCallRules.source(s, CHAT_APP, "").pkg);
        VideoCallRules.Source web = VideoCallRules.source(s, CHROME, "facetime.apple.com");
        assertEquals(CHROME, web.pkg);
        assertEquals("facetime.apple.com", web.site);
        assertNull(VideoCallRules.source(s, CHROME, "example.com"));
        // A site only counts in a browser the server listed.
        assertNull(VideoCallRules.source(s, "com.example.other", "facetime.apple.com"));
    }

    @Test
    public void onlyTheSwitchClosesASite() {
        assertNull(VideoCallRules.blockedSite(settings(true, false, 20), CHROME, "facetime.apple.com"));
        assertEquals("facetime.apple.com", VideoCallRules.blockedSite(settings(false, true, 20), CHROME, "facetime.apple.com"));
        assertNull(VideoCallRules.blockedSite(settings(false, true, 20), CHROME, "example.com"));
    }

    @Test
    public void framesWaitForACallThenComeAtTheIntervalUpToTheCap() {
        VideoCallRules.Settings s = settings(true, false, 2);
        VideoCallRules.Tracker t = new VideoCallRules.Tracker();
        VideoCallRules.Source src = VideoCallRules.source(s, CHROME, "facetime.apple.com");
        // The site open without a call (the link's landing page): nothing.
        assertNull(t.observe(s, src, false, 0));
        assertNull(t.current());
        // A call starts: the first frame waits a few seconds.
        assertNull(t.observe(s, src, true, 1_000));
        VideoCallRules.Call first = t.observe(s, src, true, 12_000);
        assertNotNull(first);
        t.taken(first, 12_000);
        assertNull(t.observe(s, src, true, 30_000));
        VideoCallRules.Call second = t.observe(s, src, true, 57_000);
        assertSame(first, second);
        t.taken(second, 57_000);
        // The cap: no more frames of this call, even once the interval has passed.
        assertNull(t.observe(s, src, true, 80_000));
        assertNull(t.observe(s, src, true, 105_000));
        assertSame(first, t.current());
        assertEquals(2, t.current().frames);
    }

    @Test
    public void aCallEndsAfterAQuietSpellAndTheNextOneIsNew() {
        VideoCallRules.Settings s = settings(true, false, 20);
        VideoCallRules.Tracker t = new VideoCallRules.Tracker();
        VideoCallRules.Source src = VideoCallRules.source(s, CHAT_APP, "");
        t.observe(s, src, true, 0);
        VideoCallRules.Call one = t.current();
        t.observe(s, src, false, 10_000);
        t.observe(s, src, true, 100_000);
        assertNotSame(one, t.current());
        assertFalse(one.id.equals(t.current().id));
    }

    @Test
    public void scanningOffTakesNoFrames() {
        VideoCallRules.Settings s = settings(false, true, 20);
        VideoCallRules.Tracker t = new VideoCallRules.Tracker();
        assertNull(t.observe(s, VideoCallRules.source(s, CHAT_APP, ""), true, 60_000));
        assertTrue(s.watching());
        assertFalse(VideoCallRules.Settings.off().watching());
    }

    @Test
    public void anAllBlackScreenshotIsBlank() {
        assertTrue(VideoCallRules.looksBlank(new int[] {0x000000, 0x050505, 0x0a0a0a}));
        assertFalse(VideoCallRules.looksBlank(new int[] {0x000000, 0x7f6050}));
        assertTrue(VideoCallRules.looksBlank(new int[0]));
    }
}
