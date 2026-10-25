package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.HashSet;

import org.junit.Test;

public class DeviceOwnerRulesTest {

    @Test
    public void aDeviceOwnerOnAProtectedPhoneAddsBothRestrictions() {
        assertEquals(new HashSet<>(Arrays.asList(
                        DeviceOwnerRules.DISALLOW_SAFE_BOOT, DeviceOwnerRules.DISALLOW_FACTORY_RESET)),
                DeviceOwnerRules.restrictions(true, true));
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
        assertEquals(2, DeviceOwnerRules.restrictionsToClear(true, false, true).size()); // the server said not a minor
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
        assertFalse(DeviceOwnerRules.hardLock(false, true, true, false)); // Layer 1: the soft lock sends them home
        assertFalse(DeviceOwnerRules.hardLock(true, true, false, false)); // not locked
        assertFalse(DeviceOwnerRules.hardLock(true, true, true, true)); // a parent's unlock window
        assertFalse(DeviceOwnerRules.hardLock(true, false, true, false)); // an adult's phone
    }
}
