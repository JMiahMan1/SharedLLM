package com.jarvisos.app;

import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Ways around the web filter, and what the guard does about each (Phase 11,
 * web filtering: Qustodio-style bypass blocks without device-owner rights).
 *
 * The filter is a DNS-only VPN, so anything that does not ask the phone's DNS
 * gets around it: another VPN (only one runs at a time, so starting one
 * switches ours off), a browser that resolves names itself or through its own
 * servers (Tor, the Opera browsers with their built-in VPN or proxy, cloud
 * browsers), and Android's Private DNS set to a provider's hostname. While the
 * parents have the filter on, the guard closes those apps like a blocked app
 * and tells the parents once about each way around it it finds.
 *
 * Plain Java with no Android types, JVM-tested. GuardService gathers the facts
 * (installed apps, apps with a VPN service, the Private DNS hostname, whether
 * our VPN runs and is allowed) and acts on the answers.
 */
public final class FilterBypass {

    /**
     * Apps that get around a DNS filter by design and do not declare a VPN
     * service (those are found on the phone, whatever their name), with why.
     * The one reviewed list: add to it here, with the reason. Reviewed 2026-10-24.
     */
    static final Map<String, String> KNOWN;

    static {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("org.torproject.torbrowser", "Tor Browser: resolves names inside Tor");
        m.put("org.torproject.torbrowser_alpha", "Tor Browser (alpha): resolves names inside Tor");
        m.put("org.torproject.android", "Orbot: Tor proxy for other apps");
        m.put("info.guardianproject.orfox", "Orfox: old Tor browser");
        m.put("com.opera.browser", "Opera: built-in VPN");
        m.put("com.opera.browser.beta", "Opera beta: built-in VPN");
        m.put("com.opera.gx", "Opera GX: built-in VPN");
        m.put("com.opera.mini.native", "Opera Mini: pages load through Opera's servers");
        m.put("com.cloudmosa.puffinFree", "Puffin: pages load on its own servers");
        m.put("com.cloudmosa.puffin", "Puffin: pages load on its own servers");
        m.put("app.intra", "Intra: encrypted DNS for the whole phone");
        KNOWN = Collections.unmodifiableMap(m);
    }

    /** Re-ask for Android's VPN consent at most this often while the filter should be on. */
    static final long PROMPT_EVERY_MS = 10 * 60_000L;

    /** Conditions the parents hear about (once each time they start). */
    static final String FILTER_OFF = "filter_disabled";
    static final String OTHER_VPN = "other_vpn";
    static final String PRIVATE_DNS = "private_dns";
    static final String BYPASS_APP = "bypass_app";

    private FilterBypass() {}

    /**
     * The installed apps that get around the filter: the reviewed list, plus
     * every app that declares a VPN service. Jarvis itself never counts.
     */
    public static Set<String> apps(Collection<String> installed, Collection<String> vpnApps) {
        Set<String> out = new TreeSet<>();
        if (installed != null) {
            for (String pkg : installed) {
                if (pkg != null && KNOWN.containsKey(pkg)) out.add(pkg);
            }
        }
        if (vpnApps != null) {
            for (String pkg : vpnApps) {
                if (pkg != null && !pkg.isEmpty()) out.add(pkg);
            }
        }
        out.remove(GuardRules.OWN_PACKAGE);
        return out;
    }

    /**
     * Android's Private DNS setting as {@code LinkProperties} reports it: a
     * hostname only in strict mode ("Private DNS provider hostname"), which
     * sends every lookup past the filter. Automatic mode asks the filter first
     * and falls back to it, so it is no way around.
     */
    public static boolean privateDnsBypasses(String strictHostname) {
        return strictHostname != null && !strictHostname.trim().isEmpty();
    }

    /**
     * Everything that gets around the filter right now, as condition keys:
     * {@code filter_disabled}, {@code other_vpn}, {@code private_dns|host},
     * {@code bypass_app|package}. Empty unless the filter should be on.
     *
     * @param running         our VPN is running
     * @param allowed         Android's VPN consent for Jarvis stands
     * @param otherVpn        a VPN that is not ours is up
     * @param privateDnsHost  the strict Private DNS hostname, or null
     * @param bypassApps      installed apps that get around the filter (see {@link #apps})
     */
    public static Set<String> conditions(boolean protectedPhone, boolean wanted, boolean unlocked,
                                         boolean running, boolean allowed, boolean otherVpn,
                                         String privateDnsHost, Collection<String> bypassApps) {
        Set<String> out = new TreeSet<>();
        if (!protectedPhone || !wanted || unlocked) return out;
        if (otherVpn && !running) out.add(OTHER_VPN);
        else if (!running && !allowed) out.add(FILTER_OFF);
        if (privateDnsBypasses(privateDnsHost)) out.add(PRIVATE_DNS + "|" + privateDnsHost.trim().toLowerCase(Locale.ROOT));
        if (bypassApps != null) {
            for (String pkg : bypassApps) out.add(BYPASS_APP + "|" + pkg);
        }
        return out;
    }

    /**
     * The conditions to tell the parents about: those present now that were
     * not present at the last check. One that clears and comes back is told again.
     */
    public static Set<String> toReport(Collection<String> now, Collection<String> reported) {
        Set<String> out = new TreeSet<>(now == null ? Collections.<String>emptySet() : now);
        if (reported != null) out.removeAll(reported);
        return out;
    }

    /** The tamper event for a condition key (the part before "|"). */
    public static String event(String condition) {
        int bar = condition.indexOf('|');
        return bar < 0 ? condition : condition.substring(0, bar);
    }

    /** The detail for a condition key (after "|"), or "" when there is none. */
    public static String detail(String condition) {
        int bar = condition.indexOf('|');
        return bar < 0 ? "" : condition.substring(bar + 1);
    }

    /**
     * Whether to put Android's "allow this VPN" prompt back in front of the
     * child: the parents want the filter on, Android withdrew Jarvis's consent
     * (switched off, or another VPN took over), and the last prompt was long
     * enough ago. Android lets an app ask; only the person holding the phone
     * can say yes.
     */
    public static boolean shouldPrompt(boolean protectedPhone, boolean wanted, boolean unlocked,
                                       boolean allowed, long now, long lastPromptAt) {
        if (!protectedPhone || !wanted || unlocked || allowed) return false;
        return now - lastPromptAt >= PROMPT_EVERY_MS;
    }

    /**
     * Settings' Private DNS dialog, where a provider hostname would take every
     * lookup past the filter (recognised by its English text, like the
     * permission dialog). Covered while the filter is on.
     */
    public static boolean isPrivateDnsScreen(String pkg, String lowerText) {
        if (pkg == null || !GuardRules.SETTINGS.contains(pkg) || lowerText == null) return false;
        return lowerText.contains("private dns") && lowerText.contains("hostname");
    }
}
