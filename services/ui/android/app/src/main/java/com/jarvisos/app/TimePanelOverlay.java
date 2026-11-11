package com.jarvisos.app;

import android.accessibilityservice.AccessibilityService;
import android.content.Context;
import android.graphics.PixelFormat;
import android.os.Build;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;

/**
 * The ten- and five-minute warning as a dialog over whatever app is open: how
 * long is left, today's use, "Ask for more time" and "OK". Like the lock
 * ({@link GuardOverlay}) it is a {@code TYPE_ACCESSIBILITY_OVERLAY} window
 * owned by the guard, so it shows over any app without the "draw over other
 * apps" permission. Unlike the lock, "OK" takes it away.
 */
final class TimePanelOverlay {

    private final AccessibilityService service;
    private final WindowManager windows;
    private View view;

    TimePanelOverlay(AccessibilityService service) {
        this.service = service;
        this.windows = (WindowManager) service.getSystemService(Context.WINDOW_SERVICE);
    }

    boolean isShown() {
        return view != null;
    }

    /** Show the warning (replacing one already up, so the newest minutes are the ones shown). */
    void show(String title, String text) {
        if (windows == null) return;
        hide();
        View built = GuardScreenView.buildTimePanel(service, title, text, this::hide);
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.START;
        // The child may type why they want more time.
        lp.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            lp.setFitInsetsTypes(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
        }
        try {
            windows.addView(built, lp);
            view = built;
        } catch (Exception ignored) {
            // The guard is not an Accessibility service any more: the notice still went out.
            view = null;
        }
    }

    void hide() {
        if (view != null && windows != null) {
            try {
                windows.removeView(view);
            } catch (Exception ignored) {
                // Already gone.
            }
        }
        view = null;
    }
}
