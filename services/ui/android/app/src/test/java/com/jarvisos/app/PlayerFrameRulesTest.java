package com.jarvisos.app;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class PlayerFrameRulesTest {

    @Test
    public void aPlayerIframeKeepsItsOwnNavigations() {
        assertTrue(PlayerFrameRules.keepInFrame(false, "https", "www.youtube-nocookie.com"));
        assertTrue(PlayerFrameRules.keepInFrame(false, "https", "player.vimeo.com"));
    }

    @Test
    public void theMainFrameIsAlwaysCapacitorsEvenForAPlayerHost() {
        // A target=_blank link or window.open to a player host must not replace the app page.
        assertFalse(PlayerFrameRules.keepInFrame(true, "https", "www.youtube-nocookie.com"));
        assertFalse(PlayerFrameRules.keepInFrame(true, "https", "player.vimeo.com"));
    }

    @Test
    public void otherHostsAndSchemesInAnIframeAreNotKept() {
        assertFalse(PlayerFrameRules.keepInFrame(false, "https", "www.youtube.com"));
        assertFalse(PlayerFrameRules.keepInFrame(false, "https", "vimeo.com"));
        assertFalse(PlayerFrameRules.keepInFrame(false, "https", "evil.player.vimeo.com"));
        assertFalse(PlayerFrameRules.keepInFrame(false, "http", "player.vimeo.com"));
        assertFalse(PlayerFrameRules.keepInFrame(false, "intent", "player.vimeo.com"));
        assertFalse(PlayerFrameRules.keepInFrame(false, "https", null));
        assertFalse(PlayerFrameRules.keepInFrame(false, null, "player.vimeo.com"));
    }
}
