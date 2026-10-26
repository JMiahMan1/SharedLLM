package com.jarvisos.app;

import android.accessibilityservice.AccessibilityService;
import android.content.Context;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.os.Build;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;

/**
 * The full-screen lock on a phone that is not a device owner: a
 * {@code TYPE_ACCESSIBILITY_OVERLAY} window owned by the Accessibility guard
 * ({@link GuardService}), so a screen-time lock covers the whole screen - the
 * home screen included - and Home or Back cannot leave it, which a cover
 * activity (Layer 1) cannot do.
 *
 * It shows the same screen as the Accessibility cover ({@link GuardScreenView}),
 * so a parent can still sign in (or use their offline code) to lift it, and the
 * child can ask for more time in Jarvis or message a parent in the family chat,
 * both of which are always-allowed. Only an Accessibility service may add this
 * window type, which is why it lives on the guard and not on the cover
 * activity; it needs no extra permission and is not the SYSTEM_ALERT_WINDOW
 * kind.
 *
 * On a device owner the lock is Android's own lock screen plus lock-task mode
 * instead ({@link DeviceOwnerRules}), so this is not shown there.
 */
final class GuardOverlay {

    private final AccessibilityService service;
    private final WindowManager windows;
    private View view;
    /** The lock reason the screen was built for, so it is rebuilt only when that changes. */
    private String reason;
    private boolean shown;

    GuardOverlay(AccessibilityService service) {
        this.service = service;
        this.windows = (WindowManager) service.getSystemService(Context.WINDOW_SERVICE);
    }

    /** Whether the lock is covering the screen right now. */
    boolean isShown() {
        return shown;
    }

    /**
     * Cover the screen with the lock, or rebuild it when the reason changed
     * (bedtime becoming the day's limit, say). Safe to call on every window
     * change: it does nothing when the same lock is already up.
     */
    void show() {
        if (windows == null) return;
        String now = GuardApi.lockReason(service);
        if (shown && now.equals(reason)) return;
        hide();
        View built = GuardScreenView.build(service, GuardScreenActivity.MODE_LOCKED, "", false, host);
        if (built == null) return;
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.START;
        // A tablet keeps a taskbar and, in landscape, a wide navigation strip.
        // Without NO_LIMITS the lock stops short of them, so part of the screen
        // stays visible and usable and the lock is not really full screen.
        // NO_LIMITS draws over every system bar, so the keyboard inset is the
        // only one the window still fits: a parent has to be able to type their
        // login (or the offline code) with the lock up, and that is the way out.
        lp.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            lp.setFitInsetsTypes(WindowInsets.Type.ime());
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
        }
        try {
            windows.addView(built, lp);
            view = built;
            shown = true;
            reason = now;
        } catch (Exception ignored) {
            // Not an Accessibility service any more, or the platform refused:
            // nothing else to do; the guard tries again at the next window change.
            view = null;
            shown = false;
        }
    }

    /** Take the lock down (a parent unlocked, or the lock lifted on its own). */
    void hide() {
        if (view != null && windows != null) {
            try {
                windows.removeView(view);
            } catch (Exception ignored) {
                // Already gone.
            }
        }
        view = null;
        shown = false;
        reason = null;
    }

    private final GuardScreenView.Host host = new GuardScreenView.Host() {
        @Override
        public void onUnlocked() {
            hide();
        }

        @Override
        public void onOpenJarvis() {
            openInJarvis("/");
        }
    };

    /**
     * Take the lock down and open Jarvis, which is one of the always-allowed
     * apps, so the lock stays down while it is in front and comes back on its
     * own when the child leaves it (see {@link GuardService}). Asking for more
     * time and messaging a parent never come through here: those are painted
     * over the lock, so the lock itself never goes away for them.
     */
    private void openInJarvis(String path) {
        hide();
        service.startActivity(new Intent(service, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(PushNotifier.EXTRA_OPEN_PATH, path));
    }
}
