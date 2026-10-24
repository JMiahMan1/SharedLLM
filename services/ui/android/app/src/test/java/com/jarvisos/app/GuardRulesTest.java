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
}
