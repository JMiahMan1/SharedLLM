package com.jarvisos.app;

import android.content.res.Configuration;
import android.graphics.Color;
import android.os.Build;
import android.view.View;
import android.view.WindowInsetsController;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Status and navigation bar look for the active theme.
 *
 * The bars are transparent and the page draws behind them, so their icons
 * must contrast with the theme: white on a dark theme, dark on a light one.
 * On a WebView too old to draw behind the bars, Capacitor pads the window
 * instead and fills the strip with the theme's window background, which is
 * white in the system's light mode: two white bars over a dark app. The strip
 * is painted the page's own background here.
 *
 * Named "JarvisSystemBars": Capacitor 8 ships a core plugin called
 * "SystemBars", and that one resets the strip and icons on every
 * configuration change (rotation, a dark-mode switch), so the look is applied
 * again after it.
 */
@CapacitorPlugin(name = "JarvisSystemBars")
public class SystemBarsPlugin extends Plugin {

    private boolean light = false;
    private Integer background = null;

    @PluginMethod
    public void setScheme(PluginCall call) {
        light = "light".equals(call.getString("scheme"));
        String color = call.getString("background");
        if (color != null) {
            try {
                background = Color.parseColor(color);
            } catch (IllegalArgumentException ignored) {
                // A colour the page could not name keeps the last good one.
            }
        }
        getActivity().runOnUiThread(() -> {
            apply();
            call.resolve();
        });
    }

    @Override
    protected void handleOnConfigurationChanged(Configuration newConfig) {
        super.handleOnConfigurationChanged(newConfig);
        // After Capacitor's own SystemBars has reset everything.
        View decor = getActivity().getWindow().getDecorView();
        decor.post(this::apply);
    }

    private void apply() {
        View decor = getActivity().getWindow().getDecorView();
        if (background != null) {
            decor.setBackgroundColor(background);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsetsController c = getActivity().getWindow().getInsetsController();
            if (c != null) {
                int mask = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                        | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                c.setSystemBarsAppearance(light ? mask : 0, mask);
            }
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            int flags = decor.getSystemUiVisibility();
            int mask = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) mask |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            decor.setSystemUiVisibility(light ? flags | mask : flags & ~mask);
        }
    }
}
