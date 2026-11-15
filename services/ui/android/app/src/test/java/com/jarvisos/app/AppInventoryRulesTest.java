package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collections;
import java.util.Set;
import java.util.TreeSet;

import org.junit.Test;

public class AppInventoryRulesTest {

    @Test
    public void onlyAppsAPersonInstalledAreListedAndNeverJarvis() {
        assertTrue(AppInventoryRules.listed("com.roblox.client", 0, GuardRules.OWN_PACKAGE));
        assertFalse(AppInventoryRules.listed("com.android.settings", AppInventoryRules.FLAG_SYSTEM, GuardRules.OWN_PACKAGE));
        assertFalse(AppInventoryRules.listed("com.google.android.youtube",
                AppInventoryRules.FLAG_SYSTEM | AppInventoryRules.FLAG_UPDATED_SYSTEM_APP, GuardRules.OWN_PACKAGE));
        assertFalse(AppInventoryRules.listed(GuardRules.OWN_PACKAGE, 0, GuardRules.OWN_PACKAGE));
        assertFalse(AppInventoryRules.listed("", 0, GuardRules.OWN_PACKAGE));
    }

    @Test
    public void dangerousPermissionsBecomeTheServersGroups() {
        Set<String> groups = AppInventoryRules.groups(Arrays.asList(
                "android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION",
                "android.permission.RECORD_AUDIO", "android.permission.INTERNET", "android.permission.READ_MEDIA_IMAGES",
                "android.permission.BLUETOOTH_CONNECT", null));
        assertEquals(new TreeSet<>(Arrays.asList("location", "microphone", "nearby", "photos")), groups);
        assertTrue(AppInventoryRules.groups(Collections.singletonList("android.permission.INTERNET")).isEmpty());
        assertTrue(AppInventoryRules.groups(null).isEmpty());
    }

    @Test
    public void everyGroupIsOneTheServerKnows() {
        Set<String> server = new TreeSet<>(Arrays.asList(
                "location", "camera", "microphone", "contacts", "sms", "phone", "photos", "calendar", "nearby"));
        Set<String> seen = AppInventoryRules.groups(Arrays.asList(
                "android.permission.ACCESS_FINE_LOCATION", "android.permission.CAMERA", "android.permission.RECORD_AUDIO",
                "android.permission.READ_CONTACTS", "android.permission.READ_SMS", "android.permission.READ_CALL_LOG",
                "android.permission.READ_MEDIA_VIDEO", "android.permission.READ_CALENDAR",
                "android.permission.NEARBY_WIFI_DEVICES"));
        assertEquals(server, seen);
    }

    @Test
    public void aNeverOpenedAppHasNoLastUseRatherThanAMadeUpOne() {
        long now = 1_800_000_000_000L;
        assertNull(AppInventoryRules.lastUsed(0, 1_700_000_000_000L, now));
        assertNull(AppInventoryRules.lastUsed(1_600_000_000_000L, 1_700_000_000_000L, now));
        assertNull(AppInventoryRules.lastUsed(now + 3_600_000L, 1_700_000_000_000L, now));
        assertEquals(Long.valueOf(1_750_000_000_000L), AppInventoryRules.lastUsed(1_750_000_000_000L, 1_700_000_000_000L, now));
    }
}
