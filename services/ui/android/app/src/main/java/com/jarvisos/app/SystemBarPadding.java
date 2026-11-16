package com.jarvisos.app;

import android.app.Activity;
import android.view.View;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;

/**
 * Keeps a plain (non-WebView) screen clear of the status bar, the navigation
 * bar, a display cutout and the keyboard.
 *
 * <p>The app targets SDK 36, so Android 15+ draws every activity edge to edge:
 * a layout that does nothing about it puts its title under the clock and its
 * last button under the nav bar (the widget setup screens did both). Call
 * {@link #apply} after {@code setContentView}; the strips take the screen's own
 * colour so the bars sit on it.
 */
public final class SystemBarPadding {

    private SystemBarPadding() {}

    public static void apply(Activity activity, int background) {
        View content = activity.findViewById(android.R.id.content);
        if (content == null) return;
        content.setBackgroundColor(background);
        ViewCompat.setOnApplyWindowInsetsListener(content, (v, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars()
                    | WindowInsetsCompat.Type.displayCutout()
                    | WindowInsetsCompat.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsetsCompat.CONSUMED;
        });
        ViewCompat.requestApplyInsets(content);
    }
}
