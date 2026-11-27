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
        // A flat monochrome glyph: the shade masks a tile icon to one colour, so the
        // full-colour launcher icon used to arrive as an unrecognisable white blob.
        tile.setIcon(android.graphics.drawable.Icon.createWithResource(this, R.drawable.ic_jarvis_tile));
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
        // A tile click is a user interaction, but a microphone foreground service
        // still cannot be started from the background on Android 12+. Open the
        // assistant instead: it is visible, so the service may start from there.
        Intent intent = new Intent(this, AssistActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            PendingIntent pending = PendingIntent.getActivity(this, 0, intent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            startActivityAndCollapse(pending);
        } else {
            // Before 34 there is no PendingIntent overload, and the Intent one is
            // deprecated (and gone from newer platform jars), so just start it.
            startActivity(intent);
        }
    }
}
