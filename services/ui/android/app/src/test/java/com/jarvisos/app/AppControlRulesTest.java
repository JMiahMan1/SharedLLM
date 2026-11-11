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
    public void warningsComeAtTenFiveAndOneMinuteAndNotBefore() {
        long bedtime = 1_000_000_000L;
        assertNull(LockWarning.due(bedtime - 601_000, bedtime, "bedtime", "phone"));
        LockWarning.Warning ten = LockWarning.due(bedtime - 599_000, bedtime, "bedtime", "phone");
        assertEquals(10, ten.stage);
        assertTrue(ten.dialog());
        assertEquals("10 minutes left", ten.title);
        assertEquals("Bedtime in 10 minutes. Time to finish up.", ten.text);
        LockWarning.Warning five = LockWarning.due(bedtime - 299_000, bedtime, "bedtime", "phone");
        assertEquals(5, five.stage);
        assertTrue(five.dialog());
        assertEquals("Bedtime in 5 minutes. Time to finish up.", five.text);
        // 5 min 30 s left is still the ten-minute stage.
        assertEquals(10, LockWarning.due(bedtime - 330_000, bedtime, "bedtime", "phone").stage);
        LockWarning.Warning one = LockWarning.due(bedtime - 59_000, bedtime, "bedtime", "phone");
        assertEquals(1, one.stage);
        assertFalse(one.dialog());
        assertTrue(one.text.startsWith("Bedtime in one minute"));
        assertEquals(one.key, LockWarning.due(bedtime - 10_000, bedtime, "bedtime", "phone").key);
        assertNull(LockWarning.due(bedtime + 1, bedtime, "bedtime", "phone"));
        assertNull(LockWarning.due(bedtime - 30_000, 0, "bedtime", "phone"));
    }

    @Test
    public void theLimitsWarningSaysTheMinutesLeft() {
        assertEquals("10 minutes of screen time left today.", LockWarning.due(0, 600_000, "limit", "phone").text);
        assertEquals("5 minutes of screen time left today.", LockWarning.due(0, 300_000, "limit", "phone").text);
        assertEquals("One minute of screen time left today.", LockWarning.due(0, 60_000, "limit", "phone").text);
    }

    @Test
    public void eachStageIsShownOnceAsTheLimitDrifts() {
        LockWarning.Tracker t = new LockWarning.Tracker();
        long at = 10_000_000L;
        assertNull(t.next(at - 20 * 60_000, at, "limit", "phone"));
        assertEquals(10, t.next(at - 595_000, at, "limit", "phone").stage);
        // The limit's lock time wobbles with each status: no second ten-minute warning.
        assertNull(t.next(at - 590_000, at + 30_000, "limit", "phone"));
        assertNull(t.next(at - 560_000, at, "limit", "phone"));
        assertEquals(5, t.next(at - 290_000, at, "limit", "phone").stage);
        assertNull(t.next(at - 200_000, at, "limit", "phone"));
        assertEquals(1, t.next(at - 50_000, at, "limit", "phone").stage);
        assertNull(t.next(at - 40_000, at, "limit", "phone"));
    }

    @Test
    public void moreTimeStartsTheWarningsOver() {
        LockWarning.Tracker t = new LockWarning.Tracker();
        long at = 10_000_000L;
        assertNotNull(t.next(at - 290_000, at, "limit", "phone"));
        // A parent gave 30 minutes: the lock moved well away, so its warnings come again.
        long later = at + 30 * 60_000;
        assertNull(t.next(at - 280_000, later, "limit", "phone"));
        assertEquals(10, t.next(later - 590_000, later, "limit", "phone").stage);
    }

    @Test
    public void aLockFirstSeenLateSkipsTheEarlierStages() {
        LockWarning.Tracker t = new LockWarning.Tracker();
        long at = 10_000_000L;
        LockWarning.Warning w = t.next(at - 180_000, at, "school", "phone");
        assertEquals(5, w.stage);
        assertEquals("School time starts in 3 minutes.", w.text);
        assertNull(t.next(at - 170_000, at, "school", "phone"));
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

    @Test
    public void choresFirstReturnsOnTheClockWhenGivenTimeRunsOut() {
        assertTrue(LockWarning.lockedByClock(2000, 1000, "chores"));
        assertEquals("One minute left. Then it's chores first.", LockWarning.due(970, 1000 + 59_000, "chores", "phone").text);
        assertEquals("5 minutes left. Then it's chores first.", LockWarning.due(0, 300_000, "chores", "phone").text);
    }
}
