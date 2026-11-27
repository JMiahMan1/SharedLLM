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
            // A pinned shortcut still sends this broadcast, and a broadcast is in
            // the background: Android 12+ refuses a microphone foreground service
            // from here. Fall back to the activity, which may start it.
            try {
                AssistService.turn(context, false);
            } catch (RuntimeException refused) {
                startAssist(context);
            }
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
            try {
                AssistService.turn(context, false);
            } catch (RuntimeException refused) {
                startAssist(context);
            }
        }
    }

    /** Opened in the foreground, so the turn's service may legally start. */
    private static void startAssist(Context context) {
        Intent open = new Intent(context, AssistActivity.class);
        open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        context.startActivity(open);
    }
}
