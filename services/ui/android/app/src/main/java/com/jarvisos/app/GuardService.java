package com.jarvisos.app;

import android.accessibilityservice.AccessibilityService;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Handler;
import android.os.Looper;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

import java.util.Collections;
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
 * approves first, and (6) warn a minute before a lock lands.
 *
 * The rules come from the server's status (refreshed every minute, kept for
 * offline); GuardRules, InstallApprovals, PermissionGate and LockWarning make
 * every decision. On an adult's phone it does nothing.
 */
public class GuardService extends AccessibilityService {

    private static final long REFRESH_SECONDS = 60;
    private static final long WARNING_CHECK_SECONDS = 10;
    private ScheduledExecutorService refresher;
    private final Handler main = new Handler(Looper.getMainLooper());
    private long lastCoverAt = 0;
    /** Apps installed since the guard started that a parent has not approved. */
    private volatile Set<String> awaitingApproval = Collections.emptySet();
    /** The app last in front: the one asking when a permission dialog opens. */
    private volatile String lastApp = "";
    private String lastWarningKey = "";
    private BroadcastReceiver installs;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        refresher = Executors.newSingleThreadScheduledExecutor();
        refresher.scheduleWithFixedDelay(() -> {
            if (GuardApi.refreshStatus(this)) GuardApi.reportOfflineUnlockIfDue(this);
            syncWebFilter();
            checkInstalls();
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
        if (event == null || event.getEventType() != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return;
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

    private void act(String pkg, String cls, String text) {
        String launcher = launcherPackage();
        GuardRules.Action action = GuardRules.decide(
                GuardApi.isProtected(this), GuardApi.isLocked(this), GuardApi.isUnlocked(this),
                pkg, cls, text, GuardApi.alwaysAllowed(this), launcher, GuardApi.blockedApps(this),
                awaitingApproval);
        if (action == GuardRules.Action.ALLOW) {
            if (isAnApp(pkg, launcher)) lastApp = pkg;
            return;
        }
        long now = System.currentTimeMillis();
        if (now - lastCoverAt < 700) return; // one cover per screen change, not one per event
        lastCoverAt = now;
        if (action == GuardRules.Action.BLOCK_TAMPER) {
            performGlobalAction(GLOBAL_ACTION_BACK);
            GuardApi.reportTamper(this, "uninstall_attempt");
        } else {
            performGlobalAction(GLOBAL_ACTION_HOME);
        }
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
            default:
                mode = GuardScreenActivity.MODE_LOCKED;
        }
        Intent cover = new Intent(this, GuardScreenActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(GuardScreenActivity.EXTRA_MODE, mode)
                .putExtra(GuardScreenActivity.EXTRA_APP, GuardNotices.label(this, pkg))
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
        String label = GuardNotices.label(this, app);
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
                GuardApi.askParent(this, "app", pkg, GuardNotices.label(this, pkg));
            }
        } catch (Exception ignored) {
            // Tried again at the next refresh.
        }
    }

    /** Family Link's one-minute warning before bedtime, school, the day's limit or an app's own minutes. */
    private void checkWarning() {
        if (!GuardApi.isProtected(this) || GuardApi.isLocked(this) || GuardApi.isUnlocked(this)) return;
        LockWarning.Warning w = LockWarning.due(System.currentTimeMillis(),
                GuardApi.nextLockAt(this), GuardApi.nextLockReason(this));
        if (w == null && !lastApp.isEmpty()) {
            w = LockWarning.appDue(lastApp, GuardNotices.label(this, lastApp),
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
            if (wanted && !WebFilterService.active && android.net.VpnService.prepare(this) == null) {
                startService(new Intent(this, WebFilterService.class));
            } else if (!wanted && WebFilterService.active) {
                startService(new Intent(this, WebFilterService.class).setAction(WebFilterService.ACTION_STOP));
            }
        } catch (Exception ignored) {
            // Tried again at the next refresh.
        }
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
