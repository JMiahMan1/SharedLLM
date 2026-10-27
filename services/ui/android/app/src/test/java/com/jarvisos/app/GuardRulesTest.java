package com.jarvisos.app;

import static org.junit.Assert.assertEquals;

import com.jarvisos.app.GuardRules.Action;

import java.util.Arrays;
import java.util.List;

import org.junit.Test;

public class GuardRulesTest {

    private static final List<String> ALLOWED = Arrays.asList("com.google.android.dialer", "com.google.android.apps.messaging");
    private static final String LAUNCHER = "com.google.android.apps.nexuslauncher";

    private static Action decide(boolean protectedPhone, boolean locked, boolean unlocked, String pkg, String cls, String text) {
        return GuardRules.decide(protectedPhone, locked, unlocked, pkg, cls, text, ALLOWED, LAUNCHER, BLOCKED);
    }

    private static final List<String> BLOCKED = Arrays.asList("com.roblox.client", "com.google.android.dialer");

    @Test
    public void disconnectingTheWebFilterIsCoveredButSettingItUpIsNot() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.vpndialogs", "com.android.vpndialogs.ManageDialog", "Jarvis web filter Disconnect"));
        assertEquals(Action.ALLOW, decide(true, false, false,
                "com.android.vpndialogs", "com.android.vpndialogs.ConfirmDialog", "Connection request OK"));
    }

    @Test
    public void aBlockedAppIsClosedWhileTheRestOfThePhoneWorks() {
        assertEquals(Action.BLOCK_APP, decide(true, false, false, "com.roblox.client", "", ""));
        assertEquals(Action.ALLOW, decide(true, false, false, "com.android.chrome", "", ""));
    }

    @Test
    public void anAlwaysAllowedAppIsNeverBlockedEvenIfListed() {
        assertEquals(Action.ALLOW, decide(true, false, false, "com.google.android.dialer", "", ""));
    }

    @Test
    public void aParentUnlockLiftsAppBlocksToo() {
        assertEquals(Action.ALLOW, decide(true, false, true, "com.roblox.client", "", ""));
    }

    @Test
    public void theUninstallDialogForJarvisIsCovered() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.google.android.packageinstaller", "com.android.packageinstaller.UninstallerActivity", "Do you want to uninstall Jarvis OS?"));
    }

    @Test
    public void jarvissAppInfoAndDeviceAdminPagesAreCovered() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.SubSettings", "App info Jarvis OS Uninstall Force stop"));
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.DeviceAdminAdd", "Deactivate this device admin app"));
    }

    @Test
    public void otherSettingsAndOtherAppsUninstallsAreLeftAlone() {
        assertEquals(Action.ALLOW, decide(true, false, false, "com.android.settings", "com.android.settings.Settings", "Wi-Fi Bluetooth"));
        assertEquals(Action.ALLOW, decide(true, false, false,
                "com.google.android.packageinstaller", "com.android.packageinstaller.InstallStart", "Do you want to install this game?"));
    }

    @Test
    public void theGuardsOwnAccessibilityDetailPageIsCovered() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings",
                "com.android.settings.accessibility.ToggleAccessibilityServicePreferenceFragment",
                "Jarvis screen guard Use service Turn off"));
    }

    @Test
    public void theFactoryResetAndErasePagesAreCovered() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.Settings$FactoryResetActivity",
                "Factory reset Erase all data"));
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.SubSettings", "Erase everything Erase all data"));
    }

    @Test
    public void theUsageAccessPageIsCovered() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.SubSettings", "Usage access"));
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.google.android.permissioncontroller",
                "com.android.permissioncontroller.settings.UsageAccessActivity", "Usage access"));
    }

    @Test
    public void theUsersAndAddUserPagesAreCovered() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.users.UserSettings", "Users Add user"));
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.SubSettings", "User & account"));
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.SubSettings", "Add user"));
    }

    @Test
    public void ordinarySettingsAndAppScreensAreNotTamperScreens() {
        // "Data usage" must not read as "usage access".
        assertEquals(Action.ALLOW, decide(true, false, false, "com.android.settings", "com.android.settings.Settings",
                "Network & internet Wi-Fi Bluetooth Data usage Battery"));
        // A normal permission dialog names neither Jarvis nor a tamper page.
        assertEquals(Action.ALLOW, decide(true, false, false,
                "com.google.android.permissioncontroller",
                "com.android.permissioncontroller.permission.ui.GrantPermissionsActivity",
                "Allow Chrome to access your location? While using the app"));
        // The same words outside Settings are not the guard's to block.
        assertEquals(Action.ALLOW, decide(true, false, false, "com.android.chrome", "",
                "Users factory reset usage access"));
    }

    @Test
    public void anAdultsPhoneIsNeverGuarded() {
        assertEquals(Action.ALLOW, decide(false, false, false,
                "com.android.settings", "com.android.settings.SubSettings", "App info Jarvis OS Uninstall"));
    }

    @Test
    public void aParentsUnlockLiftsEverything() {
        assertEquals(Action.ALLOW, decide(true, true, true, "com.android.settings", "x", "Jarvis OS"));
        assertEquals(Action.ALLOW, decide(true, true, true, "com.epicgames.fortnite", "", ""));
    }

    @Test
    public void aLockedPhoneSendsGamesHomeButKeepsCallsTextsAndHome() {
        assertEquals(Action.BLOCK_LOCKED, decide(true, true, false, "com.epicgames.fortnite", "", ""));
        assertEquals(Action.ALLOW, decide(true, true, false, "com.google.android.dialer", "", ""));
        assertEquals(Action.ALLOW, decide(true, true, false, LAUNCHER, "", ""));
        assertEquals(Action.ALLOW, decide(true, true, false, "com.jarvisos.app", "", ""));
        assertEquals(Action.ALLOW, decide(true, true, false, "com.android.systemui", "", ""));
    }

    @Test
    public void aTamperScreenIsCoveredEvenWhenAlsoLocked() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, true, false, "com.android.settings", "com.android.settings.DeviceAdminSettings", ""));
    }

    @Test
    public void nothingOpenIsNothingToDo() {
        assertEquals(Action.ALLOW, decide(true, true, false, "", "", ""));
        assertEquals(Action.ALLOW, decide(true, true, false, null, null, null));
    }

    private static final List<String> BYPASS = Arrays.asList("org.torproject.torbrowser", "ch.protonvpn.android");

    private static Action withFilter(boolean filterOn, String pkg, String text) {
        return GuardRules.decide(true, false, false, pkg, "", text, ALLOWED, LAUNCHER, BLOCKED, null, filterOn, BYPASS);
    }

    @Test
    public void appsThatGetAroundTheFilterCloseOnlyWhileItIsOn() {
        assertEquals(Action.BLOCK_BYPASS, withFilter(true, "org.torproject.torbrowser", ""));
        assertEquals(Action.ALLOW, withFilter(false, "org.torproject.torbrowser", ""));
        assertEquals(Action.ALLOW, withFilter(true, "com.android.chrome", ""));
    }

    @Test
    public void aParentsBlockAndTheScreenTimeLockComeBeforeTheBypassBlock() {
        assertEquals(Action.BLOCK_APP, GuardRules.decide(true, false, false, "com.roblox.client", "", "",
                ALLOWED, LAUNCHER, BLOCKED, null, true, Arrays.asList("com.roblox.client")));
        assertEquals(Action.BLOCK_LOCKED, GuardRules.decide(true, true, false, "org.torproject.torbrowser", "", "",
                ALLOWED, LAUNCHER, BLOCKED, null, true, BYPASS));
    }

    @Test
    public void thePrivateDnsDialogIsCoveredWhileTheFilterIsOn() {
        String dialog = "Private DNS Off Automatic Private DNS provider hostname Save";
        assertEquals(Action.BLOCK_TAMPER, withFilter(true, "com.android.settings", dialog));
        assertEquals(Action.ALLOW, withFilter(false, "com.android.settings", dialog));
    }

    @Test
    public void aParentUnlockLiftsTheBypassBlock() {
        assertEquals(Action.ALLOW, GuardRules.decide(true, false, true, "org.torproject.torbrowser", "", "",
                ALLOWED, LAUNCHER, BLOCKED, null, true, BYPASS));
    }

    @Test
    public void aBlockedWindowIsClearedTheWayItIsShown() {
        assertEquals(GuardRules.Clear.HOME, GuardRules.clearFor(false, 1));
        assertEquals(GuardRules.Clear.EXIT_SPLIT, GuardRules.clearFor(false, 2));
        assertEquals(GuardRules.Clear.DISMISS_PIP, GuardRules.clearFor(true, 2));
        assertEquals(GuardRules.Clear.DISMISS_PIP, GuardRules.clearFor(true, 1));
    }

    @Test
    public void phoneStartsWithoutTheGuardAreCountedFromTheBootCounter() {
        assertEquals(0, GuardRules.bootsWithoutGuard(41, 42)); // the next start: the guard ran each time
        assertEquals(1, GuardRules.bootsWithoutGuard(41, 43)); // one start (safe mode) without it
        assertEquals(3, GuardRules.bootsWithoutGuard(10, 14));
        assertEquals(0, GuardRules.bootsWithoutGuard(42, 42)); // the guard reconnected in the same start
        assertEquals(0, GuardRules.bootsWithoutGuard(0, 42)); // first run: nothing to compare
        assertEquals(0, GuardRules.bootsWithoutGuard(42, 0)); // no counter on this phone
        assertEquals(0, GuardRules.bootsWithoutGuard(50, 3)); // counter reset (factory reset)
    }

    private static boolean overlay(boolean deviceOwner, boolean protectedPhone, boolean locked, boolean unlocked, String pkg) {
        return GuardRules.lockOverlay(deviceOwner, protectedPhone, locked, unlocked, pkg, ALLOWED);
    }

    @Test
    public void theLockOverlayCoversTheLauncherAndEveryOtherApp() {
        // Unlike GuardRules.decide, the launcher is NOT exempt: Home cannot dismiss the lock.
        assertEquals(true, overlay(false, true, true, false, LAUNCHER));
        assertEquals(true, overlay(false, true, true, false, "com.epicgames.fortnite"));
    }

    @Test
    public void theLockOverlayLeavesCallsTextsJarvisAndSystemScreensAlone() {
        assertEquals(false, overlay(false, true, true, false, "com.google.android.dialer"));
        assertEquals(false, overlay(false, true, true, false, "com.google.android.apps.messaging"));
        assertEquals(false, overlay(false, true, true, false, "com.jarvisos.app"));
        assertEquals(false, overlay(false, true, true, false, "com.android.systemui"));
        assertEquals(false, overlay(false, true, true, false, "com.android.emergency"));
        assertEquals(false, overlay(false, true, true, false, "com.android.phone"));
        // An incoming call can always be answered, even before the server's list arrives.
        assertEquals(false, overlay(false, true, true, false, "com.android.incallui"));
        assertEquals(false, overlay(false, true, true, false, "com.samsung.android.incallui"));
        assertEquals(false, overlay(false, true, true, false, "com.android.server.telecom"));
    }

    @Test
    public void theLockOverlayIsNotUsedOnADeviceOwnerOrWhenNotLocked() {
        assertEquals(false, overlay(true, true, true, false, "com.epicgames.fortnite")); // Layer 2 does lockNow + kiosk
        assertEquals(false, overlay(false, false, true, false, "com.epicgames.fortnite")); // an adult's phone
        assertEquals(false, overlay(false, true, false, false, "com.epicgames.fortnite")); // nothing is locked
        assertEquals(false, overlay(false, true, true, true, "com.epicgames.fortnite")); // a parent's unlock window
    }

    @Test
    public void anUnknownWindowStillGetsTheLock() {
        // With no window known yet the safe default is to cover the screen.
        assertEquals(true, overlay(false, true, true, false, ""));
        assertEquals(true, overlay(false, true, true, false, null));
    }

    @Test
    public void dateAndTimeAndDeveloperOptionsSettingsAreCoveredAsTamper() {
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.SubSettings", "Date & time Set time automatically"));
        assertEquals(Action.BLOCK_TAMPER, decide(true, false, false,
                "com.android.settings", "com.android.settings.SubSettings", "Developer options USB debugging"));
    }

    @Test
    public void kioskModeBlocksAppsNotInKioskAllowlist() {
        List<String> kiosk = Arrays.asList("org.khanacademy.android");
        assertEquals(Action.ALLOW, GuardRules.decide(true, false, false, "org.khanacademy.android", "", "",
                ALLOWED, LAUNCHER, BLOCKED, null, false, null, true, kiosk));
        assertEquals(Action.ALLOW, GuardRules.decide(true, false, false, "com.jarvisos.app", "", "",
                ALLOWED, LAUNCHER, BLOCKED, null, false, null, true, kiosk));
        assertEquals(Action.BLOCK_APP, GuardRules.decide(true, false, false, "com.android.chrome", "", "",
                ALLOWED, LAUNCHER, BLOCKED, null, false, null, true, kiosk));
    }
}
