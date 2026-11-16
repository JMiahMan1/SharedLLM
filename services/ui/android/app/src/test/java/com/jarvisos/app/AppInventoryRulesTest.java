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

    @Test
    public void riskyCapabilitiesComeFromPermissionsAndDeclaredComponents() {
        Set<String> caps = AppInventoryRules.capabilities(Arrays.asList(
                "android.permission.READ_SMS", "android.permission.SEND_SMS", "android.permission.INTERNET",
                "android.permission.ACCESS_BACKGROUND_LOCATION", "android.permission.SYSTEM_ALERT_WINDOW",
                "android.permission.REQUEST_INSTALL_PACKAGES", "android.permission.READ_CALL_LOG", null),
                Arrays.asList(AppInventoryRules.DECLARES_ACCESSIBILITY, AppInventoryRules.DECLARES_VPN, "nonsense"));
        assertEquals(new TreeSet<>(Arrays.asList("accessibility", "background_location", "call_log", "install_apps",
                "overlay", "sms_read", "sms_send", "vpn")), caps);
        assertTrue(AppInventoryRules.capabilities(null, null).isEmpty());
    }

    @Test
    public void everyCapabilityIsOneTheServerKnows() {
        Set<String> server = new TreeSet<>(Arrays.asList(
                "accessibility", "device_admin", "notification_listener", "vpn", "sms_read", "sms_send", "call_log",
                "background_location", "overlay", "install_apps"));
        Set<String> seen = AppInventoryRules.capabilities(Arrays.asList(
                "android.permission.READ_SMS", "android.permission.SEND_SMS", "android.permission.READ_CALL_LOG",
                "android.permission.ACCESS_BACKGROUND_LOCATION", "android.permission.SYSTEM_ALERT_WINDOW",
                "android.permission.REQUEST_INSTALL_PACKAGES"),
                Arrays.asList(AppInventoryRules.DECLARES_ACCESSIBILITY, AppInventoryRules.DECLARES_DEVICE_ADMIN,
                        AppInventoryRules.DECLARES_NOTIFICATION_LISTENER, AppInventoryRules.DECLARES_VPN));
        assertEquals(server, seen);
    }

    @Test
    public void aComponentCountsOnlyWhenTheSystemsBindPermissionGuardsIt() {
        assertTrue(AppInventoryRules.declares(AppInventoryRules.DECLARES_ACCESSIBILITY,
                "android.permission.BIND_ACCESSIBILITY_SERVICE"));
        assertFalse(AppInventoryRules.declares(AppInventoryRules.DECLARES_ACCESSIBILITY, null));
        assertFalse(AppInventoryRules.declares(AppInventoryRules.DECLARES_VPN,
                "android.permission.BIND_ACCESSIBILITY_SERVICE"));
        assertFalse(AppInventoryRules.declares("unknown", "android.permission.BIND_VPN_SERVICE"));
    }

    @Test
    public void aSettingsComponentListNamesItsPackages() {
        assertEquals(new TreeSet<>(Arrays.asList("com.example.reader", "org.sample.helper")),
                new TreeSet<>(AppInventoryRules.packagesIn(
                        "com.example.reader/.ReaderService:org.sample.helper/org.sample.helper.Svc::/bad:nopkg")));
        assertTrue(AppInventoryRules.packagesIn(null).isEmpty());
        assertTrue(AppInventoryRules.packagesIn("  ").isEmpty());
    }

    @Test
    public void digestsAreUppercaseHex() {
        assertEquals("00FF10AB", AppInventoryRules.hex(new byte[] {0, (byte) 0xff, 0x10, (byte) 0xab}));
    }

    @Test
    public void bigFilesAreSkippedAndTheBudgetDefersTheRest() {
        assertEquals(AppInventoryRules.HASH_NOW, AppInventoryRules.hashPlan(50L << 20, AppInventoryRules.HASH_BUDGET_BYTES));
        assertEquals(AppInventoryRules.HASH_TOO_LARGE,
                AppInventoryRules.hashPlan(AppInventoryRules.APK_MAX_BYTES + 1, AppInventoryRules.HASH_BUDGET_BYTES));
        assertEquals(AppInventoryRules.HASH_DEFERRED, AppInventoryRules.hashPlan(50L << 20, 10L << 20));
    }

    @Test
    public void aCachedHashHoldsForOneVersionOnly() {
        String sha = "ab".repeat(32);
        String entry = AppInventoryRules.cacheEntry(1_700_000_000_000L, sha);
        assertEquals(sha, AppInventoryRules.cachedHash(entry, 1_700_000_000_000L));
        assertNull(AppInventoryRules.cachedHash(entry, 1_700_000_000_001L));
        assertNull(AppInventoryRules.cachedHash("1700000000000|NOTAHASH", 1_700_000_000_000L));
        assertNull(AppInventoryRules.cachedHash(null, 1L));
        assertNull(AppInventoryRules.cachedHash("garbage", 1L));
    }
}
