package com.jarvisos.app;

import java.util.Collection;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Video calls on a protected child's phone: is one going on now, should the
 * guard take a screenshot of it, and is a video-call site blocked.
 *
 * Plain Java, JVM-tested; {@link GuardService} feeds it what the Accessibility
 * service sees. The lists are the server's ({@code video_calls} in the guard
 * status, from {@code services/common/data/video_calls.json} or a parent's
 * own lists), never literals here.
 *
 * A call is a video-call app in front, or a browser in front showing a
 * video-call site, while the phone is in a call: Android's audio mode is
 * "in communication" (WebRTC in a browser and every VoIP app set it) or a
 * camera is in use. A video-call site open without a call (a FaceTime link's
 * landing page) is not a call and is never captured.
 */
final class VideoCallRules {

    private VideoCallRules() {}

    /** The parents' choices for this phone, from the guard status. */
    static final class Settings {
        final boolean scan;
        final boolean block;
        final long intervalMs;
        final int maxFrames;
        final Set<String> apps;
        final List<String> sites;
        /** Browser package to the view ids of its address bar. */
        final Map<String, List<String>> browsers;

        Settings(boolean scan, boolean block, long intervalMs, int maxFrames, Set<String> apps,
                 List<String> sites, Map<String, List<String>> browsers) {
            this.scan = scan;
            this.block = block;
            this.intervalMs = Math.max(15_000L, intervalMs);
            this.maxFrames = Math.max(1, maxFrames);
            this.apps = apps == null ? Collections.<String>emptySet() : apps;
            this.sites = sites == null ? Collections.<String>emptyList() : sites;
            this.browsers = browsers == null ? Collections.<String, List<String>>emptyMap() : browsers;
        }

        static Settings off() {
            return new Settings(false, false, 45_000L, 20, null, null, null);
        }

        boolean watching() {
            return scan || block;
        }
    }

    /** Where a call is happening: an app, or a site in a browser. */
    static final class Source {
        final String pkg;
        final String site;

        Source(String pkg, String site) {
            this.pkg = pkg == null ? "" : pkg;
            this.site = site == null ? "" : site;
        }

        String key() {
            return pkg + "|" + site;
        }
    }

    /**
     * The host an address bar shows: "facetime.apple.com/join#v=1&p=..."
     * or "https://meet.google.com/abc" both give the host, lower case. Empty
     * for search words or nothing.
     */
    static String host(String addressBar) {
        if (addressBar == null) return "";
        String s = addressBar.trim().toLowerCase(Locale.ROOT);
        if (s.isEmpty() || s.contains(" ")) return "";
        int scheme = s.indexOf("://");
        if (scheme >= 0) s = s.substring(scheme + 3);
        for (char stop : new char[] {'/', '?', '#'}) {
            int at = s.indexOf(stop);
            if (at >= 0) s = s.substring(0, at);
        }
        int user = s.lastIndexOf('@');
        if (user >= 0) s = s.substring(user + 1);
        int port = s.indexOf(':');
        if (port >= 0) s = s.substring(0, port);
        while (s.endsWith(".")) s = s.substring(0, s.length() - 1);
        return s.contains(".") ? s : "";
    }

    /** The configured site that covers {@code host} ("zoom.us" covers "us02web.zoom.us"), or null. */
    static String matchSite(String host, Collection<String> sites) {
        String h = WebFilterRules.normalize(host);
        if (h.isEmpty() || sites == null) return null;
        for (String site : sites) {
            String s = WebFilterRules.normalize(site);
            while (s.startsWith("*.")) s = s.substring(2);
            if (s.isEmpty()) continue;
            if (h.equals(s) || h.endsWith("." + s)) return s;
        }
        return null;
    }

    /**
     * The call source in front now, or null: a listed app, or a browser
     * whose page is a listed site. {@code browserHost} is the host the
     * browser in front last showed (its address bar can scroll away in a
     * full-screen call, so the guard remembers it while that browser stays
     * in front).
     */
    static Source source(Settings s, String frontPkg, String browserHost) {
        if (frontPkg == null || frontPkg.isEmpty()) return null;
        if (s.apps.contains(frontPkg)) return new Source(frontPkg, "");
        if (s.browsers.containsKey(frontPkg)) {
            String site = matchSite(browserHost, s.sites);
            if (site != null) return new Source(frontPkg, site);
        }
        return null;
    }

    /** The site to close in front now (the Video calls switch is on and a browser shows one), or null. */
    static String blockedSite(Settings s, String frontPkg, String browserHost) {
        if (!s.block || frontPkg == null || !s.browsers.containsKey(frontPkg)) return null;
        return matchSite(browserHost, s.sites);
    }

    /**
     * Whether a sampled screenshot is blank: every sample (packed RGB) is
     * near black. An app that marks its window secure comes out black in a
     * screenshot; that is reported, not sent.
     */
    static boolean looksBlank(int[] rgb) {
        if (rgb == null || rgb.length == 0) return true;
        for (int c : rgb) {
            int r = (c >> 16) & 0xff;
            int g = (c >> 8) & 0xff;
            int b = c & 0xff;
            if (r > 16 || g > 16 || b > 16) return false;
        }
        return true;
    }

    /** One call being watched: when it started, how many frames, when the last one was taken. */
    static final class Call {
        final String id;
        final Source source;
        final long startedAt;
        int frames;
        long lastFrameAt;
        long lastSeenAt;

        Call(String id, Source source, long startedAt) {
            this.id = id;
            this.source = source;
            this.startedAt = startedAt;
            this.lastSeenAt = startedAt;
        }
    }

    /**
     * Follows calls over the guard's checks. A call ends when no call has
     * been seen for {@link #END_AFTER_MS} (a moment in another app does not
     * start a new call), or when a different app or site is in a call.
     */
    static final class Tracker {
        static final long END_AFTER_MS = 30_000L;
        /** The first frame waits this long after a call starts, so a call that is hung up at once is never captured. */
        static final long FIRST_FRAME_AFTER_MS = 10_000L;
        private Call call;

        /** The call being watched, or null. */
        Call current() {
            return call;
        }

        /**
         * One check. Returns the call to take a frame of now, or null.
         * {@code inCall}: audio in communication or a camera in use.
         */
        Call observe(Settings s, Source source, boolean inCall, long now) {
            if (call != null && now - call.lastSeenAt > END_AFTER_MS) call = null;
            if (!s.scan || source == null || !inCall) return null;
            if (call == null || !call.source.key().equals(source.key())) {
                call = new Call(newId(), source, now);
            }
            call.lastSeenAt = now;
            if (call.frames >= s.maxFrames) return null;
            if (call.frames == 0) {
                return now - call.startedAt >= FIRST_FRAME_AFTER_MS ? call : null;
            }
            return now - call.lastFrameAt >= s.intervalMs ? call : null;
        }

        /** A frame of {@code c} was taken at {@code now} (sent or not): it counts toward the cap. */
        void taken(Call c, long now) {
            if (c == null) return;
            c.frames++;
            c.lastFrameAt = now;
        }

        void reset() {
            call = null;
        }

        private static String newId() {
            return UUID.randomUUID().toString();
        }
    }
}
