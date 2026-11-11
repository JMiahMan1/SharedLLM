package com.jarvisos.app;

import android.app.Dialog;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Icon;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;

/**
 * "Screen time" in Quick Settings: one swipe down shows today's time left
 * (or used, with no limit) on the tile itself, and a tap opens the time
 * panel - left, used of how much, what locks next, and "Ask for more time" -
 * over the shade, from any app. Reads the same status the guard uses, and
 * refreshes it whenever the shade opens.
 */
public class ScreenTimeTileService extends TileService {

    private final Handler main = new Handler(Looper.getMainLooper());

    @Override
    public void onStartListening() {
        super.onStartListening();
        update();
        new Thread(() -> {
            if (GuardApi.refreshStatus(this)) main.post(this::update);
        }).start();
    }

    private void update() {
        Tile tile = getQsTile();
        if (tile == null) return;
        String line = TimeUsage.tileLine(GuardApi.lastStatus(this));
        tile.setIcon(Icon.createWithResource(this, R.drawable.mi_hourglass));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            tile.setLabel("Screen time");
            tile.setSubtitle(line);
        } else {
            tile.setLabel(line);
        }
        tile.setContentDescription("Screen time: " + line);
        tile.setState(GuardApi.lastStatus(this).length() > 0 ? Tile.STATE_ACTIVE : Tile.STATE_INACTIVE);
        tile.updateTile();
    }

    @Override
    public void onClick() {
        super.onClick();
        Dialog dialog = new Dialog(this, android.R.style.Theme_Translucent_NoTitleBar);
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
        dialog.setContentView(GuardScreenView.buildTimePanel(this, null, null, dialog::dismiss));
        Window w = dialog.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(0));
            w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
            w.clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);
            w.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        }
        showDialog(dialog);
    }
}
