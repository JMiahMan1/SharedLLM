package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collections;

import org.junit.Test;

public class LockedJarvisRulesTest {

    private static final long GRACE = 3000L;

    @Test
    public void jarvisModeNeedsTheTapAndALockInForce() {
        assertTrue(LockedJarvisRules.active(true, true, true, false));
        // Never tapped "Open Jarvis": Jarvis already open when a lock lands is covered.
        assertFalse(LockedJarvisRules.active(false, true, true, false));
        // The lock lifted, or a parent's unlock window opened.
        assertFalse(LockedJarvisRules.active(true, true, false, false));
        assertFalse(LockedJarvisRules.active(true, true, true, true));
        // Not a minor's phone: there is no guard lock to open Jarvis from.
        assertFalse(LockedJarvisRules.active(true, false, true, false));
    }

    @Test
    public void lockInForceIsLockedOutsideAnUnlockWindowOnAProtectedPhone() {
        assertTrue(LockedJarvisRules.lockInForce(true, true, false));
        assertFalse(LockedJarvisRules.lockInForce(true, true, true));
        assertFalse(LockedJarvisRules.lockInForce(true, false, false));
        assertFalse(LockedJarvisRules.lockInForce(false, true, false));
    }

    @Test
    public void goingHomeOrToAnotherAppLeavesJarvis() {
        assertTrue(LockedJarvisRules.leftJarvis("com.google.android.apps.nexuslauncher", 5000, GRACE));
        assertTrue(LockedJarvisRules.leftJarvis("com.android.chrome", 5000, GRACE));
    }

    @Test
    public void theLockedAppStillFocusedWhileJarvisStartsDoesNotCount() {
        assertFalse(LockedJarvisRules.leftJarvis("com.roblox.client", 500, GRACE));
        assertTrue(LockedJarvisRules.leftJarvis("com.roblox.client", GRACE + 1, GRACE));
    }

    @Test
    public void jarvisItselfTheSystemUiACallOrTheKeyguardAreNotLeaving() {
        assertFalse(LockedJarvisRules.leftJarvis(GuardRules.OWN_PACKAGE, 60000, GRACE));
        assertFalse(LockedJarvisRules.leftJarvis("com.android.systemui", 60000, GRACE));
        assertFalse(LockedJarvisRules.leftJarvis("com.android.incallui", 60000, GRACE));
        assertFalse(LockedJarvisRules.leftJarvis("com.android.server.telecom", 60000, GRACE));
        // Android's keyguard over everything: no app window in front at all.
        assertFalse(LockedJarvisRules.leftJarvis("", 60000, GRACE));
        assertFalse(LockedJarvisRules.leftJarvis(null, 60000, GRACE));
    }

    @Test
    public void jarvisShowsOverTheLockScreenOnlyForADropInOrJarvisMode() {
        assertTrue(LockedJarvisRules.showWhenLocked(false, true));
        assertTrue(LockedJarvisRules.showWhenLocked(true, false));
        assertTrue(LockedJarvisRules.showWhenLocked(true, true));
        assertFalse(LockedJarvisRules.showWhenLocked(false, false));
    }

    @Test
    public void aDropInEndingKeepsJarvisModeOverTheLockScreen() {
        // DropInPlugin.ended clears the call's part only; Jarvis mode still holds.
        boolean dropInAfterEnd = false;
        assertTrue(LockedJarvisRules.showWhenLocked(dropInAfterEnd, true));
    }

    @Test
    public void onlyADropInWakesTheScreen() {
        assertTrue(LockedJarvisRules.turnScreenOn(true));
        assertFalse(LockedJarvisRules.turnScreenOn(false));
    }

    @Test
    public void sectionsKeepTheServersOrderWithoutBlanksOrRepeats() {
        assertEquals(Arrays.asList("chat", "bible", "chores"),
                LockedJarvisRules.sections(Arrays.asList("chat", " bible ", "", null, "chores", "bible")));
        assertEquals(Collections.emptyList(), LockedJarvisRules.sections(null));
        assertEquals(Collections.emptyList(), LockedJarvisRules.sections(Collections.<String>emptyList()));
    }
}
