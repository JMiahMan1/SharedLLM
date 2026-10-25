package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collections;
import java.util.Set;
import java.util.TreeSet;

import org.junit.Test;

public class FilterBypassTest {

    @Test
    public void knownBypassBrowsersAndEveryVpnAppCountButJarvisNever() {
        Set<String> apps = FilterBypass.apps(
                Arrays.asList("org.torproject.torbrowser", "com.android.chrome", "com.opera.browser"),
                Arrays.asList("ch.protonvpn.android", GuardRules.OWN_PACKAGE));
        assertEquals(new TreeSet<>(Arrays.asList("ch.protonvpn.android", "com.opera.browser", "org.torproject.torbrowser")), apps);
    }

    @Test
    public void anOrdinaryBrowserIsNotABypass() {
        assertTrue(FilterBypass.apps(Collections.singletonList("com.android.chrome"), null).isEmpty());
        assertTrue(FilterBypass.apps(null, null).isEmpty());
    }

    @Test
    public void everyListedAppSaysWhy() {
        for (String reason : FilterBypass.KNOWN.values()) assertFalse(reason.trim().isEmpty());
    }

    @Test
    public void onlyAStrictPrivateDnsHostnameGetsAround() {
        assertTrue(FilterBypass.privateDnsBypasses("dns.google"));
        assertFalse(FilterBypass.privateDnsBypasses(null));
        assertFalse(FilterBypass.privateDnsBypasses("  "));
    }

    @Test
    public void nothingCountsUnlessTheParentsWantTheFilterOnAProtectedPhone() {
        assertTrue(FilterBypass.conditions(true, false, false, false, false, true, "dns.google",
                Collections.singletonList("com.opera.browser")).isEmpty());
        assertTrue(FilterBypass.conditions(false, true, false, false, false, true, "dns.google", null).isEmpty());
        // A parent unlocked the phone: nothing is a way around for now.
        assertTrue(FilterBypass.conditions(true, true, true, false, false, true, "dns.google", null).isEmpty());
    }

    @Test
    public void eachWayAroundIsItsOwnCondition() {
        Set<String> c = FilterBypass.conditions(true, true, false, true, true, false, "DNS.Google",
                Collections.singletonList("com.opera.browser"));
        assertEquals(new TreeSet<>(Arrays.asList("bypass_app|com.opera.browser", "private_dns|dns.google")), c);
    }

    @Test
    public void anotherVpnInPlaceOfTheFilterIsNamedRatherThanJustOff() {
        assertEquals(Collections.singleton("other_vpn"),
                FilterBypass.conditions(true, true, false, false, false, true, null, null));
        assertEquals(Collections.singleton("filter_disabled"),
                FilterBypass.conditions(true, true, false, false, false, false, null, null));
        // Allowed but not started yet (the guard starts it within a minute): not a way around.
        assertTrue(FilterBypass.conditions(true, true, false, false, true, false, null, null).isEmpty());
    }

    @Test
    public void theParentsHearOncePerEpisode() {
        Set<String> now = new TreeSet<>(Arrays.asList("other_vpn", "bypass_app|x"));
        assertEquals(now, FilterBypass.toReport(now, null));
        assertTrue(FilterBypass.toReport(now, now).isEmpty());
        // Cleared and back: told again.
        assertEquals(Collections.singleton("other_vpn"),
                FilterBypass.toReport(now, Collections.singleton("bypass_app|x")));
    }

    @Test
    public void conditionKeysSplitIntoEventAndDetail() {
        assertEquals("bypass_app", FilterBypass.event("bypass_app|com.opera.browser"));
        assertEquals("com.opera.browser", FilterBypass.detail("bypass_app|com.opera.browser"));
        assertEquals("other_vpn", FilterBypass.event("other_vpn"));
        assertEquals("", FilterBypass.detail("other_vpn"));
    }

    @Test
    public void theVpnPromptComesBackEveryTenMinutesWhileConsentIsMissing() {
        long t = 1_000_000_000L;
        assertTrue(FilterBypass.shouldPrompt(true, true, false, false, t, 0L));
        assertFalse(FilterBypass.shouldPrompt(true, true, false, false, t, t - 60_000L));
        assertTrue(FilterBypass.shouldPrompt(true, true, false, false, t, t - FilterBypass.PROMPT_EVERY_MS));
        assertFalse(FilterBypass.shouldPrompt(true, true, false, true, t, 0L)); // consent stands
        assertFalse(FilterBypass.shouldPrompt(true, false, false, false, t, 0L)); // filter not wanted
        assertFalse(FilterBypass.shouldPrompt(true, true, true, false, t, 0L)); // a parent unlocked
        assertFalse(FilterBypass.shouldPrompt(false, true, false, false, t, 0L)); // an adult's phone
    }

    @Test
    public void thePrivateDnsDialogIsRecognisedButNotTheNetworkPage() {
        assertTrue(FilterBypass.isPrivateDnsScreen("com.android.settings",
                "private dns off automatic private dns provider hostname enter hostname of dns provider save"));
        assertFalse(FilterBypass.isPrivateDnsScreen("com.android.settings",
                "network & internet wi-fi mobile network private dns automatic"));
        assertFalse(FilterBypass.isPrivateDnsScreen("com.example.notes", "private dns provider hostname"));
    }
}
