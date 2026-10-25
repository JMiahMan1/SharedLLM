package com.jarvisos.app;

import android.accessibilityservice.AccessibilityService;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.net.VpnService;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.UserManager;
import android.provider.Settings;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Jarvis's screen guard on a child's phone (Phase 11: time budgets, app
 * control, tamper resistance). An Accessibility service sees each window that
 * opens, so it can (1) cover any screen that would uninstall Jarvis or switch
 * its protection off with a parent-login screen, (2) send the child home
 * while screen time is up, except for the always-allowed apps (calls, texts,
 * Jarvis), (3) close an app a parent blocked or whose own minutes are used up,
 * (4) keep an app installed since the guard started closed until a parent
 * approves it, (5) dismiss Android's permission dialog for a kind a parent
 * approves first, (6) warn a minute before a lock lands, and (7) keep the web
 * filter from being worked around (FilterBypass): close apps that get around
 * a DNS filter while it is on, cover the Private DNS dialog, put Android's VPN
 * prompt back when the filter was switched off, and tell the parents.
 *
 * Every visible window counts, not only the one in front: an app kept on
 * screen in split screen or picture-in-picture is closed like any other. A
 * phone start in which the guard never ran (safe mode) is reported at the
 * next normal start, and a second user or guest (which has no guard) is
 * reported as soon as it is seen.
 *
 * The rules come from the server's status (refreshed every minute, kept for
 * offline); GuardRules, InstallApprovals, PermissionGate and LockWarning make
 * every decision. On an adult's phone it does nothing.
 *
 * On a phone set up as a device owner (Layer 2, docs/HEALTH_HUB.md) it goes
 * further through DevicePolicyManager (DeviceOwnerGuard, decisions in pure
 * DeviceOwnerRules): the uninstall is blocked outright, safe boot and the
 * factory reset are disallowed, and a screen-time lock is Android's own lock
 * screen (lockNow) instead of only sending the child home.
 */
public class GuardService extends AccessibilityService {

    private static final long REFRESH_SECONDS = 60;
    private static final long WARNING_CHECK_SECONDS = 10;
    /** How long Android takes to end a split screen before going home works on the whole screen. */
    private static final long SPLIT_SETTLE_MS = 400;
    private ScheduledExecutorService refresher;
    private final Handler main = new Handler(Looper.getMainLooper());
    private long lastCoverAt = 0;
    /** Apps installed since the guard started that a parent has not approved. */
    private volatile Set<String> awaitingApproval = Collections.emptySet();
    /** The app last in front: the one asking when a permission dialog opens. */
    private volatile String lastApp = "";
    private String lastWarningKey = "";
    private BroadcastReceiver installs;
    /** Installed apps that get around the web filter (FilterBypass.apps). */
    private volatile Set<String> bypassApps = Collections.emptySet();
    /**
     * Layer 2: whether Android's lock screen has already been put up for the
     * screen-time lock in force, so it goes up once per lock and not every refresh
     * (which would re-lock the child away from the parent-unlock screen).
     */
    private volatile boolean hardLocked = false;
    /**
     * A second user or guest was seen on this protected phone (reported once
     * per guard run): that user has no Jarvis and no guard, so it is a way out.
     */
    private volatile boolean multiUserReported = false;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        refresher = Executors.newSingleThreadScheduledExecutor();
        refresher.execute(this::checkBoots);
        refresher.scheduleWithFixedDelay(() -> {
            if (GuardApi.refreshStatus(this)) {
                GuardApi.reportOfflineUnlockIfDue(this);
                GuardApi.flushTamper(this);
            }
            // Layer 2: re-apply the device-owner enforcement (uninstall block,
            // safe-boot / factory-reset restrictions) while this is a device owner,
            // and put Android's own lock screen up once when a screen-time lock
            // starts (the Accessibility cover below is then the way back in).
            DeviceOwnerGuard.enforce(this);
            boolean hard = hardLockWanted();
            if (hard && !hardLocked) DeviceOwnerGuard.lockNow(this);
            hardLocked = hard;
            // Layer 2: a remote wipe a parent queued (Phase 12 MDM) erases the
            // phone. Only a device owner may, so the pure decision gates it.
            if (DeviceOwnerRules.shouldWipe(DeviceOwnerGuard.isOwner(this), GuardApi.wipeRequested(this))) {
                DeviceOwnerGuard.wipeData(this);
            }
            syncWebFilter();
            checkInstalls();
            checkFilterBypass();
            checkMultiUser();
            // A lock that starts while the child is already in an app.
            main.post(this::checkCurrentWindow);
        }, 0, REFRESH_SECONDS, TimeUnit.SECONDS);
        refresher.scheduleWithFixedDelay(() -> main.post(this::checkWarning),
                WARNING_CHECK_SECONDS, WARNING_CHECK_SECONDS, TimeUnit.SECONDS);
        // A new install is held at once, not at the next refresh.
        installs = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                if (refresher != null) refresher.execute(() -> {
                    checkInstalls();
                    checkFilterBypass();
                    main.post(GuardService.this::checkCurrentWindow);
                });
            }
        };
        IntentFilter filter = new IntentFilter(Intent.ACTION_PACKAGE_ADDED);
        filter.addDataScheme("package");
        ContextCompat.registerReceiver(this, installs, filter, ContextCompat.RECEIVER_NOT_EXPORTED);
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;
        if (event.getEventType() == AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
            // A window joined or left the screen (split screen, picture-in-picture).
            checkVisibleWindows();
            return;
        }
        if (event.getEventType() != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return;
        CharSequence pkg = event.getPackageName();
        CharSequence cls = event.getClassName();
        StringBuilder text = new StringBuilder();
        for (CharSequence t : event.getText()) text.append(t).append(' ');
        collectText(getRootInActiveWindow(), text, 0);
        String p = pkg == null ? "" : pkg.toString();
        String c = cls == null ? "" : cls.toString();
        if (holdPermission(p, c, text.toString())) return;
        act(p, c, text.toString());
    }

    private void checkCurrentWindow() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null || root.getPackageName() == null) return;
        StringBuilder text = new StringBuilder();
        collectText(root, text, 0);
        act(root.getPackageName().toString(), "", text.toString());
    }

    private GuardRules.Action decide(String pkg, String cls, String text, String launcher) {
        return GuardRules.decide(
                GuardApi.isProtected(this), GuardApi.isLocked(this), GuardApi.isUnlocked(this),
                pkg, cls, text, GuardApi.alwaysAllowed(this), launcher, GuardApi.blockedApps(this),
                awaitingApproval, GuardApi.webFilterWanted(this), bypassApps);
    }

    /** The window in front just changed. */
    private void act(String pkg, String cls, String text) {
        String launcher = launcherPackage();
        GuardRules.Action action = decide(pkg, cls, text, launcher);
        if (action == GuardRules.Action.ALLOW) {
            if (isAnApp(pkg, launcher)) lastApp = pkg;
            // The app in front is fine; one beside it or floating over it may not be.
            checkVisibleWindows();
            return;
        }
        block(action, pkg, text, GuardRules.clearFor(false, appWindows(visibleWindows())), null);
    }

    /**
     * Every app window on screen, not only the one in front: a blocked app
     * kept visible in split screen or picture-in-picture is closed too.
     */
    private void checkVisibleWindows() {
        List<AccessibilityWindowInfo> windows = visibleWindows();
        int appWindows = appWindows(windows);
        String launcher = launcherPackage();
        for (AccessibilityWindowInfo w : windows) {
            if (w.getType() != AccessibilityWindowInfo.TYPE_APPLICATION) continue;
            AccessibilityNodeInfo root = w.getRoot();
            if (root == null || root.getPackageName() == null) continue;
            String pkg = root.getPackageName().toString();
            StringBuilder text = new StringBuilder();
            collectText(root, text, 0);
            GuardRules.Action action = decide(pkg, "", text.toString(), launcher);
            if (action == GuardRules.Action.ALLOW) continue;
            boolean pip = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && w.isInPictureInPictureMode();
            block(action, pkg, text.toString(), GuardRules.clearFor(pip, appWindows), root);
            return;
        }
    }

    /** Take a blocked window off the screen and show why. */
    private void block(GuardRules.Action action, String pkg, String text, GuardRules.Clear clear,
                       AccessibilityNodeInfo pipRoot) {
        long now = System.currentTimeMillis();
        if (now - lastCoverAt < 700) return; // one cover per screen change, not one per event
        lastCoverAt = now;
        if (action == GuardRules.Action.BLOCK_TAMPER) {
            GuardApi.reportTamper(this,
                    FilterBypass.isPrivateDnsScreen(pkg, text.toLowerCase(java.util.Locale.ROOT))
                            ? "private_dns_attempt" : "uninstall_attempt");
        }
        if (clear == GuardRules.Clear.EXIT_SPLIT) {
            // End the split first; going home straight away can leave the other half showing.
            performGlobalAction(GLOBAL_ACTION_TOGGLE_SPLIT_SCREEN);
            main.postDelayed(() -> {
                performGlobalAction(GLOBAL_ACTION_HOME);
                cover(action, pkg);
            }, SPLIT_SETTLE_MS);
            return;
        }
        boolean dismissed = false;
        if (clear == GuardRules.Clear.DISMISS_PIP && pipRoot != null) {
            // Android's picture-in-picture window offers "dismiss" to accessibility services.
            dismissed = pipRoot.performAction(AccessibilityNodeInfo.ACTION_DISMISS);
        }
        if (action == GuardRules.Action.BLOCK_TAMPER && clear == GuardRules.Clear.HOME) {
            performGlobalAction(GLOBAL_ACTION_BACK);
        } else if (!dismissed) {
            performGlobalAction(GLOBAL_ACTION_HOME);
        }
        cover(action, pkg);
    }

    /**
     * Layer 2: whether a screen-time lock should be Android's own lock screen now
     * (a device owner, on a protected phone, locked, and not inside a parent's
     * unlock window). The lock screen goes up once when a lock starts; after that
     * the Accessibility cover is the way back in, so calls, texts and Jarvis still
     * open. On a phone that is not a device owner this is always false and the
     * home-redirect above is the whole of the lock, as before.
     */
    private boolean hardLockWanted() {
        return DeviceOwnerRules.hardLock(DeviceOwnerGuard.isOwner(this), GuardApi.isProtected(this),
                GuardApi.isLocked(this), GuardApi.isUnlocked(this));
    }

    /** The guard's screen saying why, over whatever was closed. */
    private void cover(GuardRules.Action action, String pkg) {
        String mode;
        switch (action) {
            case BLOCK_TAMPER:
                mode = GuardScreenActivity.MODE_TAMPER;
                break;
            case BLOCK_APP:
                mode = GuardScreenActivity.MODE_APP;
                break;
            case BLOCK_INSTALL:
                mode = GuardScreenActivity.MODE_INSTALL;
                break;
            case BLOCK_BYPASS:
                mode = GuardScreenActivity.MODE_BYPASS;
                break;
            default:
                mode = GuardScreenActivity.MODE_LOCKED;
        }
        Intent cover = new Intent(this, GuardScreenActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(GuardScreenActivity.EXTRA_MODE, mode)
                .putExtra(GuardScreenActivity.EXTRA_APP, UnifiedPushDistributor.label(this, pkg))
                .putExtra(GuardScreenActivity.EXTRA_DENIED,
                        InstallApprovals.denied(pkg, GuardApi.appApprovals(this)));
        startActivity(cover);
    }

    /**
     * Android's "Allow ... to ...?" dialog for a kind of permission a parent
     * approves first: dismissed (which denies it) and the parents are asked.
     */
    private boolean holdPermission(String pkg, String cls, String text) {
        if (!PermissionGate.isGrantDialog(pkg, cls)) return false;
        if (!GuardApi.isProtected(this) || GuardApi.isUnlocked(this)) return false;
        String group = PermissionGate.group(text);
        String app = lastApp;
        Map<String, String> approvals = GuardApi.permissionApprovals(this);
        if (!PermissionGate.holds(GuardApi.permissionGating(this), approvals, app, group)) return false;
        performGlobalAction(GLOBAL_ACTION_BACK);
        String label = UnifiedPushDistributor.label(this, app);
        if (!approvals.containsKey(PermissionGate.target(app, group))) {
            new Thread(() -> GuardApi.askParent(this, "permission", PermissionGate.target(app, group), label)).start();
        }
        startActivity(new Intent(this, GuardScreenActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(GuardScreenActivity.EXTRA_MODE, GuardScreenActivity.MODE_PERMISSION)
                .putExtra(GuardScreenActivity.EXTRA_APP, label)
                .putExtra(GuardScreenActivity.EXTRA_DENIED,
                        InstallApprovals.DENIED.equals(approvals.get(PermissionGate.target(app, group)))));
        return true;
    }

    /** Which apps wait for a parent's OK; the parents are asked about each new one once. */
    private void checkInstalls() {
        try {
            if (!GuardApi.isProtected(this)) {
                awaitingApproval = Collections.emptySet();
                return;
            }
            Set<String> installed = GuardNotices.userApps(this);
            Set<String> baseline = GuardApi.installBaseline(this, installed);
            Map<String, String> approvals = GuardApi.appApprovals(this);
            Set<String> waiting = InstallApprovals.awaiting(GuardApi.installApprovalRequired(this), installed, baseline, approvals);
            awaitingApproval = waiting;
            for (String pkg : InstallApprovals.toAsk(waiting, approvals)) {
                GuardApi.askParent(this, "app", pkg, UnifiedPushDistributor.label(this, pkg));
            }
        } catch (Exception ignored) {
            // Tried again at the next refresh.
        }
    }

    /**
     * Ways around the web filter (FilterBypass): which installed apps get
     * around it, whether Private DNS or another VPN does, the parents told once
     * about each, and Android's VPN prompt put back when the filter's consent
     * was withdrawn.
     */
    private void checkFilterBypass() {
        try {
            boolean prot = GuardApi.isProtected(this);
            boolean wanted = GuardApi.webFilterWanted(this);
            boolean unlocked = GuardApi.isUnlocked(this);
            Set<String> apps = prot
                    ? FilterBypass.apps(GuardNotices.userApps(this), GuardNotices.vpnApps(this))
                    : Collections.<String>emptySet();
            bypassApps = apps;
            boolean allowed = VpnService.prepare(this) == null;
            boolean running = WebFilterService.active;
            Set<String> now = FilterBypass.conditions(prot, wanted, unlocked, running, allowed,
                    !running && GuardNotices.otherVpnUp(this), GuardNotices.privateDnsHost(this), apps);
            Set<String> reported = GuardApi.bypassReported(this);
            for (String c : FilterBypass.toReport(now, reported)) {
                String event = FilterBypass.event(c);
                String detail = FilterBypass.detail(c);
                if (FilterBypass.BYPASS_APP.equals(event)) detail = UnifiedPushDistributor.label(this, detail);
                GuardApi.reportTamper(this, event, detail);
            }
            GuardApi.setBypassReported(this, now);
            long t = System.currentTimeMillis();
            if (FilterBypass.shouldPrompt(prot, wanted, unlocked, allowed, t, GuardApi.lastFilterPrompt(this))) {
                Intent consent = VpnService.prepare(this);
                if (consent != null) {
                    GuardApi.setLastFilterPrompt(this, t);
                    startActivity(consent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                }
            }
        } catch (Exception ignored) {
            // Tried again at the next refresh.
        }
    }

    /**
     * Safe mode runs no downloaded app, so the guard cannot stop it; it can
     * notice afterwards. Android counts phone starts: a gap since the guard's
     * last run means starts without it, reported to the parents now.
     */
    private void checkBoots() {
        try {
            int boot = Settings.Global.getInt(getContentResolver(), Settings.Global.BOOT_COUNT, 0);
            if (boot <= 0) return;
            int missed = GuardRules.bootsWithoutGuard(GuardApi.lastGuardBoot(this), boot);
            if (missed > 0 && GuardApi.isProtected(this)) {
                GuardApi.reportTamper(this, "boot_without_guard", missed == 1 ? "1 start" : missed + " starts");
            }
            GuardApi.setLastGuardBoot(this, boot);
        } catch (Exception ignored) {
            // No boot counter on this phone: nothing to compare.
        }
    }

    /**
     * A second user or guest has no Jarvis and no guard, so it is a way out.
     * UserManager counts the users on the phone and says whether this one is the
     * system user; a protected phone should have only that one. Reported once
     * per guard run, through the same tamper path as everything else.
     */
    private void checkMultiUser() {
        try {
            if (!GuardApi.isProtected(this) || GuardApi.isUnlocked(this)) return;
            UserManager um = (UserManager) getSystemService(Context.USER_SERVICE);
            if (um == null) return;
            if (um.getUserCount() > 1 || !um.isSystemUser()) {
                if (!multiUserReported) {
                    multiUserReported = true;
                    GuardApi.reportTamper(this, "multi_user");
                }
            }
        } catch (Exception ignored) {
            // No user manager on this phone: nothing to compare.
        }
    }

    /** Family Link's one-minute warning before bedtime, school, the day's limit or an app's own minutes. */
    private void checkWarning() {
        if (!GuardApi.isProtected(this) || GuardApi.isLocked(this) || GuardApi.isUnlocked(this)) return;
        LockWarning.Warning w = LockWarning.due(System.currentTimeMillis(),
                GuardApi.nextLockAt(this), GuardApi.nextLockReason(this));
        if (w == null && !lastApp.isEmpty()) {
            w = LockWarning.appDue(lastApp, UnifiedPushDistributor.label(this, lastApp),
                    GuardApi.appMinutesLeft(this).get(lastApp), GuardApi.statusDay(this));
        }
        if (w == null || w.key.equals(lastWarningKey)) return;
        lastWarningKey = w.key;
        GuardNotices.warn(this, w.text);
        Toast.makeText(this, w.text, Toast.LENGTH_LONG).show();
    }

    private boolean isAnApp(String pkg, String launcher) {
        return pkg != null && !pkg.isEmpty() && !pkg.equals(launcher)
                && !GuardRules.NEVER_LOCKED.contains(pkg)
                && !GuardRules.SETTINGS.contains(pkg)
                && !PermissionGate.CONTROLLERS.contains(pkg);
    }

    /** Run the web filter while the parents want it (once Android's VPN consent was given). */
    private void syncWebFilter() {
        try {
            boolean wanted = GuardApi.isProtected(this) && GuardApi.webFilterWanted(this);
            if (wanted && !WebFilterService.active && VpnService.prepare(this) == null) {
                startService(new Intent(this, WebFilterService.class));
            } else if (!wanted && WebFilterService.active) {
                startService(new Intent(this, WebFilterService.class).setAction(WebFilterService.ACTION_STOP));
            }
        } catch (Exception ignored) {
            // Tried again at the next refresh.
        }
    }

    private List<AccessibilityWindowInfo> visibleWindows() {
        try {
            List<AccessibilityWindowInfo> w = getWindows();
            return w == null ? Collections.<AccessibilityWindowInfo>emptyList() : w;
        } catch (Exception e) {
            return Collections.emptyList();
        }
    }

    /** App windows sharing the screen (two or more: split screen); a picture-in-picture window floats and does not count. */
    private static int appWindows(List<AccessibilityWindowInfo> windows) {
        int n = 0;
        for (AccessibilityWindowInfo w : windows) {
            if (w.getType() != AccessibilityWindowInfo.TYPE_APPLICATION) continue;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && w.isInPictureInPictureMode()) continue;
            n++;
        }
        return n;
    }

    /** Visible text of a window, depth-limited: enough to see "Jarvis OS" on a Settings page. */
    private static void collectText(AccessibilityNodeInfo node, StringBuilder out, int depth) {
        if (node == null || depth > 12 || out.length() > 4000) return;
        CharSequence t = node.getText();
        if (t != null) out.append(t).append(' ');
        CharSequence d = node.getContentDescription();
        if (d != null) out.append(d).append(' ');
        for (int i = 0; i < node.getChildCount(); i++) collectText(node.getChild(i), out, depth + 1);
    }

    private String launcherPackage() {
        Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME);
        ResolveInfo info = getPackageManager().resolveActivity(home, PackageManager.MATCH_DEFAULT_ONLY);
        return info != null && info.activityInfo != null ? info.activityInfo.packageName : "";
    }

    @Override
    public void onInterrupt() {
        // Nothing to interrupt: the guard reacts to window changes only.
    }

    @Override
    public boolean onUnbind(Intent intent) {
        // Switched off in Settings (or killed): tell the parents on a child's phone.
        if (GuardApi.isProtected(this) && !GuardApi.isUnlocked(this)) {
            GuardApi.reportTamper(this, "guard_disabled");
        }
        if (installs != null) {
            try {
                unregisterReceiver(installs);
            } catch (Exception ignored) {
                // never registered
            }
        }
        if (refresher != null) refresher.shutdownNow();
        return super.onUnbind(intent);
    }
}
