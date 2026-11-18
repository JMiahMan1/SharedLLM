package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import org.junit.Test;

public class LockCommsRulesTest {

    private static final String JARVIS = "com.jarvisos.app";
    private static final String SMS = "com.google.android.apps.messaging";
    private static final String DIALER = "com.google.android.dialer";
    private static final String DAD = "+15550100101";
    private static final String MOM = "+442079460999";
    private static final List<String> PARENTS = Arrays.asList(DAD, MOM);

    private static Set<String> set(String... items) {
        return new LinkedHashSet<>(Arrays.asList(items));
    }

    /** A phone with a Galaxy Watch, a Pixel Watch, Fitbit, Garmin, Messages, WhatsApp, the dialer and Jarvis. */
    private static final Set<String> INSTALLED = set(
            JARVIS, SMS, DIALER, "com.whatsapp", "com.samsung.android.app.watchmanager",
            "com.samsung.android.heartplugin", "com.samsung.wearable.watch9plugin",
            "com.google.android.apps.wear.companion", "com.fitbit.FitbitMobile",
            "com.garmin.android.apps.connectmobile", "com.android.phone", "com.android.server.telecom",
            "com.example.reader", "com.example.watchlink");

    private static Set<String> locked(Collection<String> extra) {
        return LockCommsRules.lockSuspensions(true, true, true, false, INSTALLED, extra, SMS, DIALER, JARVIS);
    }

    @Test
    public void theLockSuspendsEveryWatchCompanionAndMessagingAppInstalled() {
        assertEquals(set(SMS, "com.whatsapp", "com.samsung.android.app.watchmanager",
                        "com.samsung.android.heartplugin", "com.samsung.wearable.watch9plugin",
                        "com.google.android.apps.wear.companion", "com.fitbit.FitbitMobile",
                        "com.garmin.android.apps.connectmobile"),
                new HashSet<>(locked(null)));
    }

    @Test
    public void aWatchPluginNotYetNamedIsCaughtByItsPrefix() {
        assertTrue(locked(null).contains("com.samsung.wearable.watch9plugin"));
    }

    @Test
    public void theDefaultSmsAppIsSuspendedEvenWhenNoListNamesIt() {
        Set<String> installed = new HashSet<>(INSTALLED);
        installed.add("com.example.sms");
        assertTrue(LockCommsRules.lockSuspensions(true, true, true, false, installed, null,
                "com.example.sms", DIALER, JARVIS).contains("com.example.sms"));
    }

    @Test
    public void theServersExtraListAddsAppsButOnlyInstalledOnes() {
        Set<String> out = locked(Arrays.asList("com.example.watchlink", "com.example.notinstalled"));
        assertTrue(out.contains("com.example.watchlink"));
        assertFalse(out.contains("com.example.notinstalled"));
    }

    @Test
    public void theDialerTelecomEmergencyAndJarvisAreNeverSuspended() {
        Set<String> out = locked(Arrays.asList(DIALER, JARVIS, "com.android.phone", "com.android.server.telecom",
                "com.android.emergency", "com.android.incallui"));
        for (String keep : Arrays.asList(DIALER, JARVIS, "com.android.phone", "com.android.server.telecom")) {
            assertFalse(keep, out.contains(keep));
        }
        Set<String> installed = new HashSet<>(INSTALLED);
        installed.add("com.example.dialer");
        assertFalse("a third-party default dialer is kept too",
                LockCommsRules.lockSuspensions(true, true, true, false, installed,
                        Collections.singleton("com.example.dialer"), SMS, "com.example.dialer", JARVIS)
                        .contains("com.example.dialer"));
    }

    @Test
    public void noListNamesAPackageThatMustStay() {
        for (String pkg : LockCommsRules.WATCH_COMPANIONS) assertFalse(pkg, LockCommsRules.KEEP.contains(pkg));
        for (String pkg : LockCommsRules.MESSAGING_APPS) assertFalse(pkg, LockCommsRules.KEEP.contains(pkg));
        for (String pkg : GuardRules.NEVER_LOCKED) assertTrue(pkg, LockCommsRules.KEEP.contains(pkg));
    }

    @Test
    public void nothingIsSuspendedOutsideAHardLockOrWithoutDeviceOwner() {
        assertTrue(LockCommsRules.lockSuspensions(false, true, true, false, INSTALLED, null, SMS, DIALER, JARVIS).isEmpty());
        assertTrue(LockCommsRules.lockSuspensions(true, false, true, false, INSTALLED, null, SMS, DIALER, JARVIS).isEmpty());
        assertTrue(LockCommsRules.lockSuspensions(true, true, false, false, INSTALLED, null, SMS, DIALER, JARVIS).isEmpty());
        assertTrue("a parent's unlock window lifts it",
                LockCommsRules.lockSuspensions(true, true, true, true, INSTALLED, null, SMS, DIALER, JARVIS).isEmpty());
    }

    @Test
    public void withNoInstalledListTheNamedAppsAreStillSuspended() {
        Set<String> out = LockCommsRules.lockSuspensions(true, true, true, false, null, null, SMS, DIALER, JARVIS);
        assertTrue(out.contains("com.fitbit.FitbitMobile"));
        assertFalse(out.contains(DIALER));
    }

    /**
     * The guard keeps one suspended set (blocked apps plus the lock's) and
     * unsuspends what left it. Locking adds the watch and messaging apps;
     * unlocking gives back exactly those, and a blocked app stays blocked.
     */
    @Test
    public void unlockingGivesBackExactlyWhatTheLockTookAndKeepsBlockedApps() {
        // An always-allowed messenger is still suspended for the lock.
        Collection<String> always = Arrays.asList(SMS, "com.whatsapp");
        Collection<String> blocked = Arrays.asList("com.example.reader", "com.whatsapp");
        Set<String> before = new LinkedHashSet<>(DeviceOwnerRules.packagesToSuspend(
                true, true, false, blocked, null, always, JARVIS));
        assertEquals(set("com.example.reader"), before);

        Set<String> whileLocked = new LinkedHashSet<>(before);
        whileLocked.addAll(LockCommsRules.lockSuspensions(true, true, true, false, INSTALLED, null, SMS, DIALER, JARVIS));
        assertTrue(whileLocked.contains(SMS) && whileLocked.contains("com.whatsapp"));
        assertTrue(DeviceOwnerRules.packagesToUnsuspend(true, true, before, whileLocked).isEmpty());

        Set<String> after = new LinkedHashSet<>(DeviceOwnerRules.packagesToSuspend(
                true, true, false, blocked, null, always, JARVIS));
        after.addAll(LockCommsRules.lockSuspensions(true, true, false, false, INSTALLED, null, SMS, DIALER, JARVIS));
        Set<String> lift = DeviceOwnerRules.packagesToUnsuspend(true, true, whileLocked, after);
        Set<String> lockOnly = new HashSet<>(whileLocked);
        lockOnly.removeAll(before);
        assertEquals(lockOnly, lift);
        assertFalse("still blocked by a parent", lift.contains("com.example.reader"));
    }

    @Test
    public void callsGoThroughOutsideALock() {
        assertTrue(LockCommsRules.callAllowed(false, "5550100999", PARENTS, false));
    }

    @Test
    public void whileLockedOnlyAParentOrAnEmergencyNumberIsCalled() {
        assertTrue(LockCommsRules.callAllowed(true, "+1 555-010-0101", PARENTS, false));
        assertTrue(LockCommsRules.callAllowed(true, "(555) 010-0101", PARENTS, false));
        assertTrue(LockCommsRules.callAllowed(true, "020 7946 0999", PARENTS, false));
        assertFalse(LockCommsRules.callAllowed(true, "5550100999", PARENTS, false));
        assertTrue("emergency always goes through", LockCommsRules.callAllowed(true, "911", PARENTS, true));
        assertFalse("no parents known: only emergency", LockCommsRules.callAllowed(true, DAD, Collections.<String>emptyList(), false));
        assertFalse(LockCommsRules.callAllowed(true, null, PARENTS, false));
    }

    @Test
    public void numbersMatchAcrossFormats() {
        assertTrue(LockCommsRules.sameNumber("+15550100101", DAD));
        assertTrue(LockCommsRules.sameNumber("15550100101", DAD));
        assertTrue(LockCommsRules.sameNumber("555.010.0101", DAD));
        assertTrue(LockCommsRules.sameNumber("011 44 20 7946 0999", MOM));
        assertTrue(LockCommsRules.sameNumber("0044 20 7946 0999", MOM));
    }

    @Test
    public void numbersThatOnlyLookAlikeDoNotMatch() {
        assertFalse("another country with the same national digits", LockCommsRules.sameNumber("+445550100101", DAD));
        assertFalse("a fragment", LockCommsRules.sameNumber("0101", DAD));
        assertFalse("a short code", LockCommsRules.sameNumber("010 0101", DAD));
        assertFalse("a caller-ID prefix is refused, not parsed", LockCommsRules.sameNumber("*675550100101", DAD));
        assertFalse(LockCommsRules.sameNumber("5550100101,123", DAD));
        assertFalse(LockCommsRules.sameNumber("", DAD));
        assertFalse(LockCommsRules.sameNumber(DAD, ""));
        assertFalse(LockCommsRules.sameNumber(DAD, null));
    }

    @Test
    public void allOutgoingCallsStopOnlyForALockedDeviceOwnerWithoutTheFilter() {
        assertTrue(LockCommsRules.blockAllOutgoing(true, true, true, false, false));
        assertFalse("the filter does it instead", LockCommsRules.blockAllOutgoing(true, true, true, false, true));
        assertFalse(LockCommsRules.blockAllOutgoing(true, true, false, false, false));
        assertFalse(LockCommsRules.blockAllOutgoing(true, true, true, true, false));
        assertFalse(LockCommsRules.blockAllOutgoing(false, true, true, false, false));
        assertFalse(LockCommsRules.blockAllOutgoing(true, false, true, false, false));
    }

    @Test
    public void theLockRulesApplyToEveryKindOfLock() {
        assertTrue(LockCommsRules.active(true, true, false));
        assertFalse(LockCommsRules.active(true, true, true));
        assertFalse(LockCommsRules.active(false, true, false));
        assertFalse(LockCommsRules.active(true, false, false));
    }

    @Test
    public void theLockTaskAllowlistLetsTheDialerAndInCallScreensRun() {
        Set<String> out = LockCommsRules.lockTaskPackages(JARVIS, DIALER);
        assertTrue(out.contains(JARVIS));
        assertTrue(out.contains(DIALER));
        assertTrue(out.contains("com.android.server.telecom"));
        assertTrue(out.contains("com.android.incallui"));
        assertTrue(out.contains("com.android.phone"));
        assertFalse(out.contains("com.android.systemui"));
        assertFalse(LockCommsRules.lockTaskPackages(JARVIS, null).contains(null));
    }
}
