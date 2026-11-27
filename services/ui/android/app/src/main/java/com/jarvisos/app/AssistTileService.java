package com.jarvisos.app;

import android.app.PendingIntent;
import android.content.Intent;
import android.os.Build;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

/**
 * The Quick Settings tile: one tap opens the assistant.
 *
 * It deliberately does not start the microphone service itself. Android 12+
 * refuses to let a tile start a microphone foreground service - the app has to
 * be in use for that - so the tap used to die there with nothing on screen.
 * Opening the assistant activity puts the app in front, and that activity
 * starts the service legally.
 */
public final class AssistTileService extends TileService {

    @Override
    public void onStartListening() {
        super.onStartListening();
        Tile tile = getQsTile();
        if (tile == null) return;
        tile.setLabel("Ask Jarvis");
        // Without an icon the shade draws its own placeholder, which is what
        // made this look like a strange button nobody recognised.
        tile.setIcon(android.graphics.drawable.Icon.createWithResource(this, getApplicationInfo().icon));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            tile.setSubtitle("Tap to talk");
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            tile.setStateDescription("Ask Jarvis a question");
        }
        tile.setState(Tile.STATE_INACTIVE);
        tile.updateTile();
    }

    @Override
    public void onClick() {
        super.onClick();
        Intent intent = new Intent(this, AssistActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            PendingIntent pending = PendingIntent.getActivity(this, 0, intent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            startActivityAndCollapse(pending);
        } else {
            //noinspection deprecation
            startActivityAndCollapse(intent);
        }
    }
}
