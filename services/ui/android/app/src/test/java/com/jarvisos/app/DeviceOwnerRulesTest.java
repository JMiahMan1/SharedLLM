package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

import org.junit.Test;

public class DeviceOwnerRulesTest {

    @Test
    public void aDeviceOwnerOnAProtectedPhoneAddsEveryRestriction() {
        assertEquals(new HashSet<>(Arrays.asList(
                        DeviceOwnerRules.DISALLOW_SAFE_BOOT, DeviceOwnerRules.DISALLOW_FACTORY_RESET,
                        DeviceOwnerRules.DISALLOW_USER_SWITCH, DeviceOwnerRules.DISALLOW_ADD_USER,
                        DeviceOwnerRules.DISALLOW_DEBUGGING_FEATURES, DeviceOwnerRules.DISALLOW_CONFIG_VPN,
                        DeviceOwnerRules.DISALLOW_CONFIG_PRIVATE_DNS,
                        DeviceOwnerRules.DISALLOW_INSTALL_UNKNOWN_SOURCES,
                        DeviceOwnerRules.DISALLOW_INSTALL_UNKNOWN_SOURCES_GLOBALLY,
                        DeviceOwnerRules.DISALLOW_CONFIG_DATE_TIME,
                        DeviceOwnerRules.DISALLOW_APPS_CONTROL,
                        DeviceOwnerRules.DISALLOW_MOUNT_PHYSICAL_MEDIA)),
                DeviceOwnerRules.restrictions(true, true));
    }

    @Test
    public void theRestrictionValuesMatchAndroidsUserManagerConstants() {
        assertEquals("no_safe_boot", DeviceOwnerRules.DISALLOW_SAFE_BOOT);
        assertEquals("no_factory_reset", DeviceOwnerRules.DISALLOW_FACTORY_RESET);
        assertEquals("no_user_switch", DeviceOwnerRules.DISALLOW_USER_SWITCH);
        assertEquals("no_add_user", DeviceOwnerRules.DISALLOW_ADD_USER);
        assertEquals("no_debugging_features", DeviceOwnerRules.DISALLOW_DEBUGGING_FEATURES);
        assertEquals("no_config_vpn", DeviceOwnerRules.DISALLOW_CONFIG_VPN);
        assertEquals("no_config_private_dns", DeviceOwnerRules.DISALLOW_CONFIG_PRIVATE_DNS);
        assertEquals("no_install_unknown_sources", DeviceOwnerRules.DISALLOW_INSTALL_UNKNOWN_SOURCES);
        assertEquals("no_install_unknown_sources_globally", DeviceOwnerRules.DISALLOW_INSTALL_UNKNOWN_SOURCES_GLOBALLY);
        assertEquals("no_config_date_time", DeviceOwnerRules.DISALLOW_CONFIG_DATE_TIME);
        assertEquals("no_control_apps", DeviceOwnerRules.DISALLOW_APPS_CONTROL);
        assertEquals("no_physical_media", DeviceOwnerRules.DISALLOW_MOUNT_PHYSICAL_MEDIA);
    }

    @Test
    public void withoutDeviceOwnerOrOnAnAdultsPhoneNoRestrictionIsAdded() {
        assertTrue(DeviceOwnerRules.restrictions(false, true).isEmpty());
        assertTrue(DeviceOwnerRules.restrictions(true, false).isEmpty());
        assertTrue(DeviceOwnerRules.restrictions(false, false).isEmpty());
    }

    @Test
    public void theRestrictionsComeOffOnlyWhenTheServerSaysThisIsNoLongerAMinorsPhone() {
        assertTrue(DeviceOwnerRules.restrictionsToClear(true, true, true).isEmpty()); // still a minor
        assertTrue(DeviceOwnerRules.restrictionsToClear(true, false, false).isEmpty()); // no status yet: keep them
        assertEquals(12, DeviceOwnerRules.restrictionsToClear(true, false, true).size()); // the server said not a minor
        assertTrue(DeviceOwnerRules.restrictionsToClear(false, false, true).isEmpty()); // not device owner
    }

    @Test
    public void theUninstallIsBlockedOnlyForADeviceOwnerOnAProtectedPhone() {
        assertTrue(DeviceOwnerRules.blockUninstall(true, true));
        assertFalse(DeviceOwnerRules.blockUninstall(false, true));
        assertFalse(DeviceOwnerRules.blockUninstall(true, false));
        assertFalse(DeviceOwnerRules.blockUninstall(false, false));
    }

    @Test
    public void theUninstallBlockIsLiftedOnlyWhenTheServerSaysNotAMinor() {
        assertTrue(DeviceOwnerRules.unblockUninstall(true, false, true));
        assertFalse(DeviceOwnerRules.unblockUninstall(true, false, false)); // no status yet: keep the block
        assertFalse(DeviceOwnerRules.unblockUninstall(true, true, true)); // still a minor
        assertFalse(DeviceOwnerRules.unblockUninstall(false, false, true)); // not device owner
    }

    @Test
    public void alwaysOnVpnIsConfiguredOnlyWhenWantedOnADeviceOwnerProtectedPhone() {
        assertEquals("com.jarvisos.app",
                DeviceOwnerRules.alwaysOnVpnPackage(true, true, true, "com.jarvisos.app"));
        assertNull(DeviceOwnerRules.alwaysOnVpnPackage(false, true, true, "com.jarvisos.app"));
        assertNull(DeviceOwnerRules.alwaysOnVpnPackage(true, false, true, "com.jarvisos.app"));
        assertNull(DeviceOwnerRules.alwaysOnVpnPackage(true, true, false, "com.jarvisos.app"));
        assertTrue(DeviceOwnerRules.clearAlwaysOnVpn(true, true, false, true));
        assertTrue(DeviceOwnerRules.clearAlwaysOnVpn(true, false, true, true));
        assertFalse(DeviceOwnerRules.clearAlwaysOnVpn(true, false, false, false)); // status unknown: keep it
        assertFalse(DeviceOwnerRules.clearAlwaysOnVpn(true, true, true, true)); // still wanted
    }

    @Test
    public void blockedAndUnapprovedPackagesAreSuspendedAndUnsuspendedSafely() {
        Set<String> suspended = DeviceOwnerRules.packagesToSuspend(
                true, true, false,
                Arrays.asList("com.roblox.client", "com.jarvisos.app", "com.google.android.dialer"),
                Arrays.asList("com.discord"),
                Arrays.asList("com.google.android.dialer"),
                "com.jarvisos.app");
        assertEquals(new HashSet<>(Arrays.asList("com.roblox.client", "com.discord")), suspended);
        // Unlock window lifts suspension
        assertTrue(DeviceOwnerRules.packagesToSuspend(
                true, true, true,
                Arrays.asList("com.roblox.client"), Collections.emptyList(),
                Collections.emptyList(), "com.jarvisos.app").isEmpty());
        // Unblocking com.roblox.client unsuspends it while keeping com.discord suspended
        Set<String> toUnsuspend = DeviceOwnerRules.packagesToUnsuspend(
                true, true,
                Arrays.asList("com.roblox.client", "com.discord"),
                Collections.singletonList("com.discord"));
        assertEquals(Collections.singleton("com.roblox.client"), toUnsuspend);
    }

    @Test
    public void remoteUninstallAndInstallRespectSafetyCoreAndInstalledState() {
        Set<String> toRemove = DeviceOwnerRules.packagesToUninstall(
                true, true,
                Arrays.asList("com.roblox.client", "com.missing.app", "com.jarvisos.app", "com.google.android.dialer"),
                Arrays.asList("com.roblox.client", "com.jarvisos.app", "com.google.android.dialer"),
                Collections.singletonList("com.google.android.dialer"),
                "com.jarvisos.app");
        assertEquals(Collections.singleton("com.roblox.client"), toRemove);

        Set<String> toInstall = DeviceOwnerRules.packagesToInstall(
                true,
                Arrays.asList("org.khanacademy.android", "com.duolingo"),
                Collections.singletonList("com.duolingo"));
        assertEquals(Collections.singleton("org.khanacademy.android"), toInstall);
    }

    @Test
    public void kioskPackagesIncludeJarvisAlwaysAllowedAndKioskAppsWhenActive() {
        Set<String> allow = DeviceOwnerRules.kioskPackages(
                true, true, false, false, true,
                Collections.singletonList("org.khanacademy.android"),
                Collections.singletonList("com.google.android.dialer"),
                "com.jarvisos.app");
        assertTrue(allow.contains("com.jarvisos.app"));
        assertTrue(allow.contains("org.khanacademy.android"));
        assertTrue(allow.contains("com.google.android.dialer"));
        assertTrue(DeviceOwnerRules.kioskPackages(
                true, true, false, false, false,
                Collections.singletonList("org.khanacademy.android"),
                Collections.emptyList(), "com.jarvisos.app").isEmpty());
    }

    @Test
    public void aScreenTimeLockIsARealOsLockOnlyOnADeviceOwnerPhone() {
        assertTrue(DeviceOwnerRules.hardLock(true, true, true, false));
        assertFalse(DeviceOwnerRules.hardLock(false, true, true, false)); // not device owner: the Accessibility overlay locks instead
        assertFalse(DeviceOwnerRules.hardLock(true, true, false, false)); // not locked
        assertFalse(DeviceOwnerRules.hardLock(true, true, true, true)); // a parent's unlock window
        assertFalse(DeviceOwnerRules.hardLock(true, false, true, false)); // an adult's phone
    }

    @Test
    public void thePhoneIsPinnedToTheGuardScreenOnlyOnADeviceOwnerPhone() {
        assertTrue(DeviceOwnerRules.lockTask(true, true, true, false));
        assertFalse(DeviceOwnerRules.lockTask(false, true, true, false)); // not device owner: the overlay covers it instead
        assertFalse(DeviceOwnerRules.lockTask(true, true, false, false)); // not locked
        assertFalse(DeviceOwnerRules.lockTask(true, true, true, true)); // a parent's unlock window
        assertFalse(DeviceOwnerRules.lockTask(true, false, true, false)); // an adult's phone
    }

    @Test
    public void aRemoteWipeRunsOnlyOnADeviceOwnerWithAQueuedWipe() {
        assertTrue(DeviceOwnerRules.shouldWipe(true, true));
        assertFalse(DeviceOwnerRules.shouldWipe(true, false)); // nothing was requested
        assertFalse(DeviceOwnerRules.shouldWipe(false, true)); // not device owner: Android would refuse it
    }
}
