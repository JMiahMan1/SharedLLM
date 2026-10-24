package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import com.jarvisos.app.GuardRules.Action;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.junit.Test;

/** Install approvals, permission gating and the one-minute warning. */
public class AppControlRulesTest {

    private static final List<String> BASELINE = Arrays.asList("com.android.chrome", "com.spotify.music");
    private static final List<String> INSTALLED = Arrays.asList(
            "com.android.chrome", "com.spotify.music", "com.roblox.client", "com.snapchat.android", GuardRules.OWN_PACKAGE);
    private static final String LAUNCHER = "com.google.android.apps.nexuslauncher";

    private static Map<String, String> answers(String... pairs) {
        Map<String, String> out = new HashMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) out.put(pairs[i], pairs[i + 1]);
        return out;
    }

    @Test
    public void appsInstalledAfterTheGuardStartedWaitUntilApproved() {
        Set<String> waiting = InstallApprovals.awaiting(true, INSTALLED, BASELINE,
                answers("com.snapchat.android", "approved", "com.roblox.client", "pending"));
        assertEquals(Collections.singleton("com.roblox.client"), waiting);
    }

    @Test
    public void nothingWaitsWhenAParentSwitchedApprovalsOff() {
        assertTrue(InstallApprovals.awaiting(false, INSTALLED, BASELINE, answers()).isEmpty());
    }

    @Test
    public void jarvisItselfIsNeverHeld() {
        assertFalse(InstallApprovals.awaiting(true, INSTALLED, Collections.emptyList(), answers()).contains(GuardRules.OWN_PACKAGE));
    }

    @Test
    public void theParentsAreAskedOnlyAboutAppsTheyHaveNotHeardOf() {
        Set<String> waiting = InstallApprovals.awaiting(true, INSTALLED, BASELINE, answers("com.roblox.client", "denied"));
        assertEquals(Collections.singleton("com.snapchat.android"), InstallApprovals.toAsk(waiting, answers("com.roblox.client", "denied")));
        assertTrue(InstallApprovals.denied("com.roblox.client", answers("com.roblox.client", "denied")));
    }

    @Test
    public void aWaitingAppIsClosedButLocksAndTamperComeFirst() {
        List<String> waiting = Collections.singletonList("com.roblox.client");
        assertEquals(Action.BLOCK_INSTALL, GuardRules.decide(true, false, false, "com.roblox.client", "", "",
                null, LAUNCHER, null, waiting));
        assertEquals(Action.BLOCK_LOCKED, GuardRules.decide(true, true, false, "com.roblox.client", "", "",
                null, LAUNCHER, null, waiting));
        assertEquals(Action.ALLOW, GuardRules.decide(true, false, true, "com.roblox.client", "", "",
                null, LAUNCHER, null, waiting));
        assertEquals(Action.ALLOW, GuardRules.decide(false, false, false, "com.roblox.client", "", "",
                null, LAUNCHER, null, waiting));
    }

    @Test
    public void theGrantDialogAndItsKindAreRecognised() {
        assertTrue(PermissionGate.isGrantDialog("com.google.android.permissioncontroller",
                "com.android.permissioncontroller.permission.ui.GrantPermissionsActivity"));
        assertFalse(PermissionGate.isGrantDialog("com.android.settings", "GrantPermissionsActivity"));
        assertEquals("camera", PermissionGate.group("Allow Snapchat to take pictures and record video?"));
        assertEquals("location", PermissionGate.group("Allow Snapchat to access this device's location?"));
        assertEquals("microphone", PermissionGate.group("Allow Discord to record audio?"));
        assertEquals("photos", PermissionGate.group("Allow TikTok to access photos and videos on this device?"));
        assertEquals("nearby", PermissionGate.group("Allow Roblox to find, connect to, and determine the relative position of nearby devices?"));
        assertEquals("sms", PermissionGate.group("Allow App to send and view SMS messages?"));
        assertNull(PermissionGate.group("Allow App to send you notifications?"));
    }

    @Test
    public void aGatedKindIsHeldUntilAParentApprovesItForThatApp() {
        List<String> gated = Arrays.asList("camera", "location");
        Map<String, String> ok = answers("com.snapchat.android|camera", "approved");
        assertFalse(PermissionGate.holds(gated, ok, "com.snapchat.android", "camera"));
        assertTrue(PermissionGate.holds(gated, ok, "com.snapchat.android", "location"));
        assertTrue(PermissionGate.holds(gated, ok, "com.discord", "camera"));
        assertFalse(PermissionGate.holds(gated, ok, "com.discord", "microphone"));
        assertFalse(PermissionGate.holds(gated, null, GuardRules.OWN_PACKAGE, "camera"));
        assertEquals("com.discord|camera", PermissionGate.target("com.discord", "camera"));
    }

    @Test
    public void theWarningComesInTheLastMinuteOnceAndNotBefore() {
        long bedtime = 1_000_000_000L;
        assertNull(LockWarning.due(bedtime - 61_000, bedtime, "bedtime"));
        LockWarning.Warning w = LockWarning.due(bedtime - 59_000, bedtime, "bedtime");
        assertNotNull(w);
        assertTrue(w.text.startsWith("Bedtime in one minute"));
        assertEquals(w.key, LockWarning.due(bedtime - 10_000, bedtime, "bedtime").key);
        assertNull(LockWarning.due(bedtime + 1, bedtime, "bedtime"));
        assertNull(LockWarning.due(bedtime - 30_000, 0, "bedtime"));
    }

    @Test
    public void theLimitsKeyHoldsStillAsItsTimeDrifts() {
        long at = 600_000L * 1000 + 300_000;
        assertEquals(LockWarning.due(at - 30_000, at, "limit").key, LockWarning.due(at - 20_000, at + 20_000, "limit").key);
    }

    @Test
    public void anAppsLastMinuteIsWarnedByName() {
        LockWarning.Warning w = LockWarning.appDue("com.google.android.youtube", "YouTube", 1, "2026-10-24");
        assertEquals("YouTube has one minute left today.", w.text);
        assertNull(LockWarning.appDue("com.google.android.youtube", "YouTube", 2, "2026-10-24"));
        assertNull(LockWarning.appDue("com.google.android.youtube", "YouTube", null, "2026-10-24"));
    }

    @Test
    public void bedtimeLocksOnTheClockButTheLimitWaitsForTheServer() {
        assertTrue(LockWarning.lockedByClock(2000, 1000, "bedtime"));
        assertTrue(LockWarning.lockedByClock(2000, 1000, "school"));
        assertFalse(LockWarning.lockedByClock(2000, 1000, "limit"));
        assertFalse(LockWarning.lockedByClock(500, 1000, "bedtime"));
    }
}
