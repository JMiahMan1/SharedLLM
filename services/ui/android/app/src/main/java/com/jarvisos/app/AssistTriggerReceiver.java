package com.jarvisos.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;
import android.view.KeyEvent;

/**
 * The physical ways into the assistant: the headset/Bluetooth button, and the
 * tap on the home-screen widget (a broadcast, so talking needs no activity).
 *
 * A play/pause button only opens a turn on a double-press while the screen is
 * on - deciding that is AssistEntryRules' job, so the rule is tested off a
 * device.
 */
public final class AssistTriggerReceiver extends BroadcastReceiver {

    /** The widget (or a pinned shortcut) asking for a turn. */
    public static final String ACTION_TALK = "com.jarvisos.app.assist.TALK";

    private static long lastPressMs;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (ACTION_TALK.equals(action)) {
            AssistService.turn(context, false);
            return;
        }
        if (!Intent.ACTION_MEDIA_BUTTON.equals(action)) return;
        KeyEvent event = intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT);
        if (event == null) return;
        long now = SystemClock.elapsedRealtime();
        boolean twice = AssistEntryRules.doublePress(lastPressMs, now);
        if (event.getAction() == KeyEvent.ACTION_DOWN) {
            lastPressMs = now;
            return;
        }
        android.os.PowerManager power =
                (android.os.PowerManager) context.getSystemService(Context.POWER_SERVICE);
        boolean screenOn = power == null || power.isInteractive();
        if (AssistEntryRules.startsTurn(event.getKeyCode(), event.getAction(), twice, screenOn)) {
            lastPressMs = 0;
            AssistService.turn(context, false);
        }
    }
}
