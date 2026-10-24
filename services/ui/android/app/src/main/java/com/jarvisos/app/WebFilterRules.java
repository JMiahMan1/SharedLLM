package com.jarvisos.app;

import java.util.Collection;
import java.util.Locale;

/**
 * What the phone's web filter does with one DNS name (Phase 11 step 5):
 * answer "no such site" for a blocked one, send it to the family-safe or the
 * open resolver, or answer with a search engine's safe-search address.
 *
 * Plain Java, JVM-tested. A listed domain covers every name under it
 * ("tiktok.com" also blocks "www.tiktok.com"). The parent's allow list wins
 * over everything, then their block list, then safe search, then the
 * family-safe resolver (Cloudflare for Families: adult and malware sites).
 */
public final class WebFilterRules {

    /** Cloudflare for Families: blocks malware and adult content. */
    static final String FAMILY_RESOLVER = "1.1.1.3";
    static final String OPEN_RESOLVER = "1.1.1.1";

    public enum Kind { BLOCK, FORWARD, SAFE }

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
