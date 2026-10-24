package com.jarvisos.app;

import android.accessibilityservice.AccessibilityService;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Handler;
import android.os.Looper;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * Jarvis's screen guard on a child's phone (Phase 11: time budgets, tamper
 * resistance). An Accessibility service sees each window that opens, so it can
 * (1) cover any screen that would uninstall Jarvis or switch its protection
 * off with a parent-login screen, and (2) send the child home while screen
 * time is up, except for the always-allowed apps (calls, texts, Jarvis).
 *
 * The rules come from the server's status (refreshed every minute, kept for
 * offline); GuardRules makes every decision. On an adult's phone it does
 * nothing.
 */
public class GuardService extends AccessibilityService {

    private static final long REFRESH_SECONDS = 60;
    private ScheduledExecutorService refresher;
    private final Handler main = new Handler(Looper.getMainLooper());
    private long lastCoverAt = 0;

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        refresher = Executors.newSingleThreadScheduledExecutor();
        refresher.scheduleWithFixedDelay(() -> {
            GuardApi.refreshStatus(this);
            // A lock that starts while the child is already in an app.
            main.post(this::checkCurrentWindow);
        }, 0, REFRESH_SECONDS, TimeUnit.SECONDS);
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null || event.getEventType() != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return;
        CharSequence pkg = event.getPackageName();
        CharSequence cls = event.getClassName();
        StringBuilder text = new StringBuilder();
        for (CharSequence t : event.getText()) text.append(t).append(' ');
        collectText(getRootInActiveWindow(), text, 0);
        act(pkg == null ? "" : pkg.toString(), cls == null ? "" : cls.toString(), text.toString());
    }

    private void checkCurrentWindow() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null || root.getPackageName() == null) return;
        StringBuilder text = new StringBuilder();
        collectText(root, text, 0);
        act(root.getPackageName().toString(), "", text.toString());
    }

    private void act(String pkg, String cls, String text) {
        GuardRules.Action action = GuardRules.decide(
                GuardApi.isProtected(this), GuardApi.isLocked(this), GuardApi.isUnlocked(this),
                pkg, cls, text, GuardApi.alwaysAllowed(this), launcherPackage());
        if (action == GuardRules.Action.ALLOW) return;
        long now = System.currentTimeMillis();
        if (now - lastCoverAt < 700) return; // one cover per screen change, not one per event
        lastCoverAt = now;
        if (action == GuardRules.Action.BLOCK_TAMPER) {
            performGlobalAction(GLOBAL_ACTION_BACK);
            GuardApi.reportTamper(this, "uninstall_attempt");
        } else {
            performGlobalAction(GLOBAL_ACTION_HOME);
        }
        Intent cover = new Intent(this, GuardScreenActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .putExtra(GuardScreenActivity.EXTRA_MODE,
                        action == GuardRules.Action.BLOCK_TAMPER ? GuardScreenActivity.MODE_TAMPER : GuardScreenActivity.MODE_LOCKED);
        startActivity(cover);
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
        if (refresher != null) refresher.shutdownNow();
        return super.onUnbind(intent);
    }
}
