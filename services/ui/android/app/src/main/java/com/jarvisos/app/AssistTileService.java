package com.jarvisos.app;

import android.os.Build;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

/**
 * "Talk to Jarvis" in the Quick Settings shade: the entry point for a phone
 * with no spare button and no room for a widget. Modelled on the screen-time
 * tile, which is the app's existing Quick Settings precedent.
 */
public final class AssistTileService extends TileService {

    @Override
    public void onStartListening() {
        super.onStartListening();
        Tile tile = getQsTile();
        if (tile != null) {
            tile.setLabel("Jarvis");
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                tile.setSubtitle("Talk");
            }
            tile.setState(Tile.STATE_INACTIVE);
            tile.updateTile();
        }
    }

    @Override
    public void onClick() {
        super.onClick();
        AssistService.turn(this, false);
    }
}
