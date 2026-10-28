package com.jarvisos.app;

import android.app.PendingIntent;
import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.os.Build;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Collections;
import java.util.Set;

/**
 * Layer 2 (device owner): the Android calls behind {@link DeviceOwnerRules},
 * made on a phone provisioned as a device owner (docs/HEALTH_HUB.md). Every
 * decision is in the pure, JVM-tested {@code DeviceOwnerRules}; this class only
 * asks DevicePolicyManager and PackageInstaller to apply it.
 *
 * Without device owner every method here does nothing: the Accessibility guard
 * (Layer 1) is then the whole of the protection, as before.
 */
final class DeviceOwnerGuard {

    private DeviceOwnerGuard() {}

    /** Whether this app is the phone's device owner. */
    static boolean isOwner(Context context) {
        DevicePolicyManager dpm = dpm(context);
        return dpm != null && dpm.isDeviceOwnerApp(context.getPackageName());
    }

    /**
     * Re-applies the device-owner enforcement: the uninstall block while the
     * phone is a protected child's, the user restrictions, always-on VPN when
     * the web filter is wanted, OS-level suspension of blocked/unapproved apps,
     * silent remote uninstall of packages in {@code uninstall_apps}, and the
     * COSU kiosk lock-task allowlist. Idempotent, so it runs on every status
     * refresh.
     */
    static void enforce(Context context) {
        enforce(context, Collections.<String>emptySet(), Collections.<String>emptySet());
    }

    static void enforce(Context context, Set<String> awaitingApproval, Set<String> installedApps) {
        DevicePolicyManager dpm = dpm(context);
        boolean owner = dpm != null && dpm.isDeviceOwnerApp(context.getPackageName());
        if (!owner) return;
        boolean prot = GuardApi.isProtected(context);
        boolean known = GuardApi.statusKnown(context);
        boolean unlocked = GuardApi.isUnlocked(context);
        boolean locked = GuardApi.isLocked(context);
        boolean filterWanted = GuardApi.webFilterWanted(context);
        String ownPkg = context.getPackageName();
        ComponentName admin = new ComponentName(context, GuardAdminReceiver.class);
        // The values in DeviceOwnerRules mirror Android's UserManager.DISALLOW_* constants.
        for (String key : DeviceOwnerRules.restrictions(owner, prot)) {
            try {
                dpm.addUserRestriction(admin, key);
            } catch (Exception ignored) {
                // An older Android without this restriction: the others still apply.
            }
        }
        for (String key : DeviceOwnerRules.restrictionsToClear(owner, prot, known)) {
            try {
                dpm.clearUserRestriction(admin, key);
            } catch (Exception ignored) {
                // Not set: nothing to take off.
            }
        }
        if (DeviceOwnerRules.blockUninstall(owner, prot)) {
            try {
                dpm.setUninstallBlocked(admin, ownPkg, true);
            } catch (Exception ignored) {
                // Not device owner any more: nothing to block.
            }
        } else if (DeviceOwnerRules.unblockUninstall(owner, prot, known)) {
            try {
                dpm.setUninstallBlocked(admin, ownPkg, false);
            } catch (Exception ignored) {
                // Nothing to lift.
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            String vpnPkg = DeviceOwnerRules.alwaysOnVpnPackage(owner, prot, filterWanted, ownPkg);
            boolean lockdown = DeviceOwnerRules.alwaysOnVpnLockdown(owner, prot, GuardApi.vpnLockdown(context));
            if (vpnPkg != null) {
                try {
                    dpm.setAlwaysOnVpnPackage(admin, vpnPkg, lockdown);
                } catch (Exception ignored) {
                    // VPN service not ready yet; GuardService starts it on the next tick.
                }
            } else if (DeviceOwnerRules.clearAlwaysOnVpn(owner, prot, filterWanted, known)) {
                try {
                    dpm.setAlwaysOnVpnPackage(admin, null, false);
                } catch (Exception ignored) {
                    // Nothing to clear.
                }
            }
            Set<String> always = GuardApi.alwaysAllowed(context);
            Set<String> nowSuspended = DeviceOwnerRules.packagesToSuspend(
                    owner, prot, unlocked, GuardApi.blockedApps(context), awaitingApproval, always, ownPkg);
            Set<String> toUnsuspend = DeviceOwnerRules.packagesToUnsuspend(
                    owner, known, GuardApi.suspendedPackages(context), nowSuspended);
            if (!nowSuspended.isEmpty()) {
                try {
                    dpm.setPackagesSuspended(admin, nowSuspended.toArray(new String[0]), true);
                } catch (Exception ignored) {
                    // Platform refused one or more packages.
                }
            }
            if (!toUnsuspend.isEmpty()) {
                try {
                    dpm.setPackagesSuspended(admin, toUnsuspend.toArray(new String[0]), false);
                } catch (Exception ignored) {
                    // Nothing to unsuspend.
                }
            }
            if (known || !nowSuspended.isEmpty()) {
                GuardApi.setSuspendedPackages(context, nowSuspended);
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            String effPolicy = DeviceOwnerRules.effectiveUpdatePolicy(owner, prot, GuardApi.osUpdatePolicyType(context));
            try {
                if (DeviceOwnerRules.UPDATE_POLICY_AUTOMATIC.equals(effPolicy)) {
                    dpm.setSystemUpdatePolicy(admin, android.app.admin.SystemUpdatePolicy.createAutomaticInstallPolicy());
                } else if (DeviceOwnerRules.UPDATE_POLICY_WINDOWED.equals(effPolicy)) {
                    Integer start = GuardApi.osUpdateWindowStart(context);
                    Integer end = GuardApi.osUpdateWindowEnd(context);
                    if (DeviceOwnerRules.validWindow(start, end)) {
                        dpm.setSystemUpdatePolicy(admin, android.app.admin.SystemUpdatePolicy.createWindowedInstallPolicy(start, end));
                    } else {
                        dpm.setSystemUpdatePolicy(admin, null);
                    }
                } else if (DeviceOwnerRules.UPDATE_POLICY_POSTPONE.equals(effPolicy)) {
                    dpm.setSystemUpdatePolicy(admin, android.app.admin.SystemUpdatePolicy.createPostponeInstallPolicy());
                } else {
                    dpm.setSystemUpdatePolicy(admin, null);
                }
            } catch (Exception ignored) {
                // Policy could not be set on this device firmware.
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            org.json.JSONArray certs = GuardApi.caCerts(context);
            if (certs != null && owner && prot) {
                for (int i = 0; i < certs.length(); i++) {
                    org.json.JSONObject c = certs.optJSONObject(i);
                    if (c == null) continue;
                    String pem = DeviceOwnerRules.cleanCaCertPem(c.optString("cert_pem", ""));
                    boolean enabled = c.optBoolean("enabled", true);
                    if (pem == null) continue;
                    try {
                        java.security.cert.CertificateFactory cf = java.security.cert.CertificateFactory.getInstance("X.509");
                        java.io.ByteArrayInputStream bis = new java.io.ByteArrayInputStream(pem.getBytes(java.nio.charset.StandardCharsets.UTF_8));
                        java.security.cert.Certificate cert = cf.generateCertificate(bis);
                        byte[] der = cert.getEncoded();
                        if (enabled) {
                            if (!dpm.hasCaCertInstalled(admin, der)) {
                                dpm.installCaCert(admin, der);
                            }
                        } else {
                            if (dpm.hasCaCertInstalled(admin, der)) {
                                dpm.uninstallCaCert(admin, der);
                            }
                        }
                    } catch (Exception ignored) {
                        // Certificate format or installation issue
                    }
                }
            }
        }
        org.json.JSONArray wifis = GuardApi.wifiNetworks(context);
        if (wifis != null && owner && prot) {
            try {
                android.net.wifi.WifiManager wm = (android.net.wifi.WifiManager) context.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
                if (wm != null) {
                    for (int i = 0; i < wifis.length(); i++) {
                        org.json.JSONObject net = wifis.optJSONObject(i);
                        if (net == null) continue;
                        String ssid = DeviceOwnerRules.cleanSsid(net.optString("ssid", ""));
                        if (ssid == null) continue;
                        String password = net.optString("password", "");
                        boolean hidden = net.optBoolean("hidden", false);
                        String sec = net.optString("security", "wpa2");
                        android.net.wifi.WifiConfiguration conf = new android.net.wifi.WifiConfiguration();
                        conf.SSID = "\"" + ssid + "\"";
                        conf.hiddenSSID = hidden;
                        if ("open".equalsIgnoreCase(sec) || password.isEmpty()) {
                            conf.allowedKeyManagement.set(android.net.wifi.WifiConfiguration.KeyMgmt.NONE);
                        } else {
                            conf.preSharedKey = "\"" + password + "\"";
                            conf.allowedKeyManagement.set(android.net.wifi.WifiConfiguration.KeyMgmt.WPA_PSK);
                        }
                        wm.addNetwork(conf);
                    }
                }
            } catch (Exception ignored) {
                // WifiManager not available or restricted on this device.
            }
        }
        Set<String> toUninstall = DeviceOwnerRules.packagesToUninstall(
                owner, prot, GuardApi.uninstallApps(context), installedApps, GuardApi.alwaysAllowed(context), ownPkg);
        for (String pkg : toUninstall) {
            uninstallPackageSilently(context, pkg);
        }
        Set<String> kioskAllow = DeviceOwnerRules.kioskPackages(
                owner, prot, locked, unlocked, GuardApi.kioskMode(context),
                GuardApi.kioskApps(context), GuardApi.alwaysAllowed(context), ownPkg);
        if (!kioskAllow.isEmpty()) {
            try {
                dpm.setLockTaskPackages(admin, kioskAllow.toArray(new String[0]));
            } catch (Exception ignored) {
                // Not supported on this build.
            }
        } else if (!locked && known) {
            try {
                dpm.setLockTaskPackages(admin, new String[0]);
            } catch (Exception ignored) {
                // Nothing to clear.
            }
        }
    }

    /** Silently uninstall a package on a device-owner phone via PackageInstaller. */
    static void uninstallPackageSilently(Context context, String packageName) {
        if (!isOwner(context) || packageName == null || packageName.isEmpty()) return;
        try {
            PackageInstaller installer = context.getPackageManager().getPackageInstaller();
            int flags = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                    ? PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
                    : PendingIntent.FLAG_UPDATE_CURRENT;
            PendingIntent pi = PendingIntent.getBroadcast(
                    context, packageName.hashCode(), new Intent(context.getPackageName() + ".MDM_UNINSTALL"), flags);
            installer.uninstall(packageName, pi.getIntentSender());
        } catch (Exception ignored) {
            // Package already gone or not removable.
        }
    }

    /**
     * Silently install a verified APK on a device-owner phone via
     * {@link PackageInstaller.Session}, bypassing the interactive prompt and
     * {@code DISALLOW_INSTALL_UNKNOWN_SOURCES}. Returns true if the session was
     * committed.
     */
    static boolean installApkSilently(Context context, File apk) {
        if (!isOwner(context) || apk == null || !apk.exists()) return false;
        PackageInstaller.Session session = null;
        try {
            PackageInstaller installer = context.getPackageManager().getPackageInstaller();
            PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(
                    PackageInstaller.SessionParams.MODE_FULL_INSTALL);
            int sessionId = installer.createSession(params);
            session = installer.openSession(sessionId);
            try (InputStream in = new FileInputStream(apk);
                 OutputStream out = session.openWrite("package.apk", 0, apk.length())) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                }
                session.fsync(out);
            }
            int flags = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                    ? PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
                    : PendingIntent.FLAG_UPDATE_CURRENT;
            PendingIntent pi = PendingIntent.getBroadcast(
                    context, sessionId, new Intent(context.getPackageName() + ".MDM_INSTALL"), flags);
            session.commit(pi.getIntentSender());
            return true;
        } catch (Exception e) {
            if (session != null) {
                try {
                    session.abandon();
                } catch (Exception ignored) {
                    // Best-effort cleanup.
                }
            }
            return false;
        }
    }

    /**
     * A real OS lock (Android's own lock screen), for a screen-time lock on a
     * device owner. Call only when {@link DeviceOwnerRules#hardLock} says so;
     * without device owner the guard covers the screen with the Accessibility
     * overlay instead ({@link GuardOverlay}).
     */
    static void lockNow(Context context) {
        DevicePolicyManager dpm = dpm(context);
        if (dpm == null) return;
        try {
            dpm.lockNow();
        } catch (Exception ignored) {
            // No secure lock screen, or no longer device owner: the overlay still runs.
        }
    }

    /**
     * Allow Jarvis to hold Android's lock-task (kiosk) mode: only this package
     * may run once the guard screen starts lock task. A device owner may set the
     * allowlist; call only when {@link DeviceOwnerRules#lockTask} says so. Without
     * device owner (or on an older Android without lock task) this does nothing,
     * and the Accessibility overlay is the whole of the lock.
     */
    static void enterLockTask(Context context) {
        DevicePolicyManager dpm = dpm(context);
        if (dpm == null) return;
        try {
            dpm.setLockTaskPackages(new ComponentName(context, GuardAdminReceiver.class),
                    new String[]{context.getPackageName()});
        } catch (Exception ignored) {
            // Not device owner, or no lock task on this Android: the overlay still runs.
        }
    }

    /**
     * Take the lock-task allowlist off again, so a phone whose lock has lifted
     * is not left pinnable. Call after {@code stopLockTask()} (or once the guard
     * screen has gone), and only on a device owner.
     */
    static void exitLockTask(Context context) {
        DevicePolicyManager dpm = dpm(context);
        if (dpm == null) return;
        Set<String> kioskAllow = DeviceOwnerRules.kioskPackages(
                dpm.isDeviceOwnerApp(context.getPackageName()),
                GuardApi.isProtected(context), GuardApi.isLocked(context), GuardApi.isUnlocked(context),
                GuardApi.kioskMode(context), GuardApi.kioskApps(context),
                GuardApi.alwaysAllowed(context), context.getPackageName());
        try {
            dpm.setLockTaskPackages(new ComponentName(context, GuardAdminReceiver.class),
                    kioskAllow.toArray(new String[0]));
        } catch (Exception ignored) {
            // Not device owner: nothing to clear.
        }
    }

    /**
     * Erase the phone (a factory reset) at a parent's explicit request. Only a
     * device owner may; call only when {@link DeviceOwnerRules#shouldWipe} says
     * so. This cannot be undone: the phone comes back to its out-of-box state.
     */
    static void wipeData(Context context) {
        DevicePolicyManager dpm = dpm(context);
        if (dpm == null) return;
        try {
            dpm.wipeData(0);
        } catch (Exception ignored) {
            // No longer device owner, or the platform refused: nothing else to do.
        }
    }

    private static DevicePolicyManager dpm(Context context) {
        return (DevicePolicyManager) context.getSystemService(Context.DEVICE_POLICY_SERVICE);
    }
}
