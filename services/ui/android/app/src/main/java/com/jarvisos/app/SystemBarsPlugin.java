package com.jarvisos.app;

import android.os.Build;
import android.view.View;
import android.view.WindowInsetsController;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Status and navigation bar icon color for the active theme.
 *
 * The bars are transparent and the page draws behind them, so their icons
 * must contrast with the theme: white on a dark theme, dark on a light one.
 * Android defaults to white, which made the clock and battery vanish on a
 * light theme.
 */
@CapacitorPlugin(name = "SystemBars")
public class SystemBarsPlugin extends Plugin {

    @PluginMethod
    public void setScheme(PluginCall call) {
        boolean light = "light".equals(call.getString("scheme"));
        getActivity().runOnUiThread(() -> {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                WindowInsetsController c = getActivity().getWindow().getInsetsController();
                if (c != null) {
                    int mask = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                            | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                    c.setSystemBarsAppearance(light ? mask : 0, mask);
                }
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                View decor = getActivity().getWindow().getDecorView();
                int flags = decor.getSystemUiVisibility();
                int mask = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) mask |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
                decor.setSystemUiVisibility(light ? flags | mask : flags & ~mask);
            }
            call.resolve();
        });
    }
}
