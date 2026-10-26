package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.HashSet;

import org.junit.Test;

public class DeviceOwnerRulesTest {

    @Test
    public void aDeviceOwnerOnAProtectedPhoneAddsEveryRestriction() {
        assertEquals(new HashSet<>(Arrays.asList(
                        DeviceOwnerRules.DISALLOW_SAFE_BOOT, DeviceOwnerRules.DISALLOW_FACTORY_RESET,
                        DeviceOwnerRules.DISALLOW_USER_SWITCH, DeviceOwnerRules.DISALLOW_ADD_USER,
                        DeviceOwnerRules.DISALLOW_DEBUGGING_FEATURES, DeviceOwnerRules.DISALLOW_CONFIG_VPN,
                        DeviceOwnerRules.DISALLOW_CONFIG_PRIVATE_DNS)),
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
        assertEquals(7, DeviceOwnerRules.restrictionsToClear(true, false, true).size()); // the server said not a minor
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
