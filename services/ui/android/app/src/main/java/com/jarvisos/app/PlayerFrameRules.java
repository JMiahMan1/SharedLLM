package com.jarvisos.app;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * Which navigations stay inside the WebView for the private chat's inline
 * video players (PrivateLinkCard plays a tapped YouTube or Vimeo link in an
 * iframe, from the hosts in privateLinkPreview.ts playerUrl).
 *
 * Capacitor's BridgeWebViewClient sends every navigation to a host outside
 * the app to the phone's browser and does not look at which frame it is for,
 * so a redirect or a click inside a player's iframe would leave the app.
 * Listing the player hosts in capacitor.config allowNavigation would fix that
 * but also give those origins the native bridge and let a link replace the
 * app's own page with theirs. This rule is narrower: only a subframe, only
 * https, only these two hosts. Every main-frame navigation still goes through
 * Capacitor, which opens anything external in the browser.
 *
 * Plain Java, so the rule is unit-tested on the JVM.
 */
final class PlayerFrameRules {

    /** The player hosts; keep in step with playerUrl in src/lib/privateLinkPreview.ts. */
    static final Set<String> PLAYER_HOSTS = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
            "www.youtube-nocookie.com",
            "player.vimeo.com")));

    private PlayerFrameRules() {}

    /** Whether this navigation loads in its own iframe instead of going to Capacitor. */
    static boolean keepInFrame(boolean forMainFrame, String scheme, String host) {
        if (forMainFrame) return false;
        if (!"https".equals(scheme)) return false;
        return host != null && PLAYER_HOSTS.contains(host);
    }
}
