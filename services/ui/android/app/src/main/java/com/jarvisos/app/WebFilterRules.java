package com.jarvisos.app;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * What the phone's web filter does with one DNS name (Phase 11 step 5):
 * answer "no such site" for a blocked one, send it to the family-safe or the
 * open resolver, or answer with a search engine's safe-search address.
 *
 * Plain Java, JVM-tested. A listed domain covers every name under it
 * ("tiktok.com" also blocks "www.tiktok.com"). The parent's allow list wins
 * over everything, then their block list, then safe search, then the
 * family-safe resolver (Cloudflare for Families: adult and malware sites).
 *
 * Encrypted DNS (DoH) providers are answered "no such site" too, unless a
 * parent allowed one: a browser set to its own secure DNS must look its
 * provider up first, and without it falls back to the phone's DNS (this
 * filter). Firefox's canary name tells it not to switch DoH on by itself.
 */
public final class WebFilterRules {

    /** Cloudflare for Families: blocks malware and adult content. */
    static final String FAMILY_RESOLVER = "1.1.1.3";
    static final String OPEN_RESOLVER = "1.1.1.1";

    public enum Kind { BLOCK, FORWARD, SAFE }

    /** BLOCK's target for an encrypted-DNS provider: not a site, so no "Ask a parent". */
    public static final String ENCRYPTED_DNS = "encrypted-dns";

    /**
     * Encrypted DNS (DoH/DoT) provider names a browser or app would use to
     * get around the filter, each covering the names under it. The one
     * reviewed list; reviewed 2026-10-24. A provider reached by a bare IP
     * address needs no lookup and is not caught here.
     */
    static final List<String> ENCRYPTED_DNS_HOSTS = java.util.Collections.unmodifiableList(java.util.Arrays.asList(
            "use-application-dns.net", // Firefox's canary: "no such site" keeps its own DoH off
            "dns.google", "dns.google.com", "8888.google",
            "cloudflare-dns.com", "one.one.one.one",
            "dns.quad9.net", "dns9.quad9.net", "dns10.quad9.net", "dns11.quad9.net",
            "doh.opendns.com", "doh.familyshield.opendns.com",
            "dns.nextdns.io",
            "dns.adguard.com", "dns.adguard-dns.com", "dns-unfiltered.adguard.com", "family.adguard-dns.com",
            "doh.cleanbrowsing.org",
            "doh.mullvad.net", "dns.mullvad.net",
            "dns.controld.com", "freedns.controld.com"));

    /**
     * One-tap website categories a parent can block on a child's web filter.
     * Matches {@code WEB_FILTER_CATEGORIES} in {@code services/common/screen_time.py}.
     */
    static final Map<String, List<String>> CATEGORY_DOMAINS;
    static {
        Map<String, List<String>> m = new java.util.HashMap<>();
        m.put("social", java.util.Arrays.asList(
                "tiktok.com", "musical.ly", "instagram.com", "cdninstagram.com",
                "snapchat.com", "sc-cdn.net", "facebook.com", "fb.com", "fbcdn.net",
                "threads.net", "x.com", "twitter.com", "twimg.com", "reddit.com",
                "redd.it", "pinterest.com", "tumblr.com", "bereal.com", "bsky.app",
                "mastodon.social"));
        m.put("gaming", java.util.Arrays.asList(
                "roblox.com", "rbxcdn.com", "steampowered.com", "steamcommunity.com",
                "epicgames.com", "fortnite.com", "ea.com", "battle.net", "blizzard.com",
                "riotgames.com", "leagueoflegends.com", "valorant.com", "itch.io",
                "poki.com", "crazygames.com", "coolmathgames.com", "hoodamath.com",
                "krunker.io", "agar.io", "slither.io", "chess.com"));
        m.put("streaming", java.util.Arrays.asList(
                "netflix.com", "nflxvideo.net", "twitch.tv", "ttvnw.net", "kick.com",
                "hulu.com", "disneyplus.com", "max.com", "hbomax.com", "primevideo.com",
                "peacocktv.com", "paramountplus.com", "crunchyroll.com", "pluto.tv",
                "tubitv.com", "roku.com", "dailymotion.com", "vimeo.com"));
        m.put("chat", java.util.Arrays.asList(
                "discord.com", "discordapp.com", "discord.gg", "telegram.org", "t.me",
                "whatsapp.com", "signal.org", "kik.com", "omegle.com", "monkey.app",
                "yubo.live", "aminoapps.com", "4chan.org", "4channel.org", "8kun.top",
                "quora.com"));
        m.put("gambling", java.util.Arrays.asList(
                "draftkings.com", "fanduel.com", "betmgm.com", "caesars.com",
                "bet365.com", "stake.com", "roobet.com", "pokerstars.com", "bovada.lv",
                "betway.com", "888casino.com", "unibet.com", "prizepicks.com",
                "underdogfantasy.com"));
        m.put("vpn", java.util.Arrays.asList(
                "hide.me", "hidemyass.com", "nordvpn.com", "expressvpn.com",
                "surfshark.com", "protonvpn.com", "mullvad.net", "windscribe.com",
                "psiphon.ca", "torproject.org", "croxyproxy.com", "proxysite.com",
                "kproxy.com", "hidester.com", "4everproxy.com", "vpnbook.com",
                "privateinternetaccess.com", "cyberghostvpn.com"));
        CATEGORY_DOMAINS = java.util.Collections.unmodifiableMap(m);
    }

    public static final class Decision {
        public final Kind kind;
        /** FORWARD: the resolver to ask. SAFE: the safe-search host to answer with. */
        public final String target;

        Decision(Kind kind, String target) {
            this.kind = kind;
            this.target = target;
        }
    }

    private WebFilterRules() {}

    public static Decision decide(String name, boolean adultFilter, boolean safeSearch,
                                  Collection<String> block, Collection<String> allow) {
        String n = normalize(name);
        if (n.isEmpty()) return new Decision(Kind.FORWARD, adultFilter ? FAMILY_RESOLVER : OPEN_RESOLVER);
        if (covered(n, allow)) return new Decision(Kind.FORWARD, OPEN_RESOLVER);
        if (covered(n, ENCRYPTED_DNS_HOSTS)) return new Decision(Kind.BLOCK, ENCRYPTED_DNS);
        if (covered(n, block)) return new Decision(Kind.BLOCK, null);
        if (safeSearch) {
            String safe = safeHost(n);
            if (safe != null) return new Decision(Kind.SAFE, safe);
        }
        return new Decision(Kind.FORWARD, adultFilter ? FAMILY_RESOLVER : OPEN_RESOLVER);
    }

    /** The safe-search address a search engine publishes for its own names, or null. */
    static String safeHost(String n) {
        if (n.equals("forcesafesearch.google.com") || n.equals("restrict.youtube.com")
                || n.equals("strict.bing.com") || n.equals("safe.duckduckgo.com")) {
            return null;
        }
        if (n.equals("youtube.com") || n.equals("www.youtube.com") || n.equals("m.youtube.com")
                || n.equals("youtubei.googleapis.com") || n.equals("youtube.googleapis.com")
                || n.equals("www.youtube-nocookie.com")) {
            return "restrict.youtube.com";
        }
        // google.com, www.google.com, google.co.uk, www.google.com.au ...
        String g = n.startsWith("www.") ? n.substring(4) : n;
        if (g.equals("google.com") || g.matches("google\\.(com?\\.)?[a-z]{2}")) return "forcesafesearch.google.com";
        if (n.equals("bing.com") || n.equals("www.bing.com")) return "strict.bing.com";
        if (n.equals("duckduckgo.com") || n.equals("www.duckduckgo.com")) return "safe.duckduckgo.com";
        return null;
    }

    /**
     * The allow list the filter honours now: the parent's own, plus sites a
     * parent allowed for a while ("ask a parent") whose time has not run out.
     * Checked on the phone too, so an hour ends on time even offline.
     */
    public static List<String> activeAllow(Collection<String> allow, Map<String, Long> allowUntilMs, long nowMs) {
        List<String> out = new ArrayList<>();
        if (allow != null) out.addAll(allow);
        if (allowUntilMs != null) {
            for (Map.Entry<String, Long> e : allowUntilMs.entrySet()) {
                if (e.getValue() != null && e.getValue() > nowMs && !out.contains(e.getKey())) out.add(e.getKey());
            }
        }
        return out;
    }

    /**
     * The block list the filter honours now: the parent's own block list plus
     * every domain in the categories the parent switched on.
     */
    public static List<String> activeBlock(Collection<String> block, Collection<String> categories) {
        List<String> out = new ArrayList<>();
        if (block != null) out.addAll(block);
        if (categories != null) {
            for (String cat : categories) {
                List<String> domains = CATEGORY_DOMAINS.get(cat);
                if (domains == null) continue;
                for (String d : domains) {
                    if (!out.contains(d)) out.add(d);
                }
            }
        }
        return out;
    }

    /** The site a blocked name stands for, as a parent would allow it ("www." dropped). */
    public static String site(String name) {
        String n = normalize(name);
        return n.startsWith("www.") ? n.substring(4) : n;
    }

    /** The family-safe resolver answers a blocked site with 0.0.0.0. */
    public static boolean familyBlocked(List<byte[]> addresses) {
        if (addresses == null || addresses.isEmpty()) return false;
        for (byte[] ip : addresses) {
            if (ip == null || ip.length != 4) return false;
            if (ip[0] != 0 || ip[1] != 0 || ip[2] != 0 || ip[3] != 0) return false;
        }
        return true;
    }

    static final long OFFER_AGAIN_MS = 30 * 60_000L;

    /**
     * Whether to offer "Ask a parent" for a blocked site now: once per site
     * every half hour, so a page that keeps retrying does not keep buzzing.
     * Records the offer in {@code lastOffered}.
     */
    public static boolean shouldOffer(Map<String, Long> lastOffered, String site, long nowMs) {
        if (site == null || site.isEmpty() || lastOffered == null) return false;
        Long last = lastOffered.get(site);
        if (last != null && nowMs - last < OFFER_AGAIN_MS) return false;
        lastOffered.put(site, nowMs);
        return true;
    }

    static boolean covered(String n, Collection<String> domains) {
        if (domains == null) return false;
        for (String d : domains) {
            String dn = normalize(d);
            if (dn.isEmpty()) continue;
            if (n.equals(dn) || n.endsWith("." + dn)) return true;
        }
        return false;
    }

    static String normalize(String name) {
        if (name == null) return "";
        String n = name.trim().toLowerCase(Locale.ROOT);
        while (n.endsWith(".")) n = n.substring(0, n.length() - 1);
        return n;
    }
}
