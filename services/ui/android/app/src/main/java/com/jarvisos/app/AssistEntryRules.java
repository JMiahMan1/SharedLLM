package com.jarvisos.app;

/**
 * Where a person can start the phone's voice assistant, and what a physical
 * button press means (Phase: the phone as a voice assistant).
 *
 * Three ways in, because no single one works on every phone: a home-screen
 * widget or pinned shortcut, a headset/Bluetooth button, and the system
 * assistant role (long-press home). Choosing between them is the phone's
 * business, not a person's guess, so the decision lives here as plain Java and
 * is tested on the JVM.
 *
 * The key codes are spelled out as numbers on purpose: this class must not
 * import android.view.KeyEvent, or it could not be tested off a device.
 */
public final class AssistEntryRules {

    /** android.view.KeyEvent.KEYCODE_HEADSETHOOK - the button on a wired headset. */
    public static final int KEY_HEADSETHOOK = 79;
    /** KEYCODE_VOICE_ASSIST - the dedicated voice button some handsets have. */
    public static final int KEY_VOICE_ASSIST = 231;
    /** KEYCODE_MEDIA_PLAY_PAUSE - Bluetooth headsets and earbuds send this. */
    public static final int KEY_MEDIA_PLAY_PAUSE = 85;
    public static final int KEY_MEDIA_NEXT = 87;
    public static final int KEY_MEDIA_PREVIOUS = 88;

    public static final int ACTION_DOWN = 0;
    public static final int ACTION_UP = 1;

    /** Two presses inside this window are one double-press, not two presses. */
    public static final long DOUBLE_PRESS_MS = 450;
    /** The system assistant role exists (and can be requested) from Android 10. */
    public static final int ROLE_SDK = 29;

    private AssistEntryRules() {}

    /**
     * Whether this key event should open a turn.
     *
     * A headset or voice button means "talk to Jarvis" by itself, so a single
     * press is enough. Play/pause (and track skip) already mean something else,
     * so a turn only opens on a double-press, and only while the screen is on:
     * a single pause in a pocket must never start listening.
     */
    public static boolean startsTurn(int keyCode, int action, boolean doublePress, boolean screenOn) {
        if (action != ACTION_UP) return false;
        if (keyCode == KEY_HEADSETHOOK || keyCode == KEY_VOICE_ASSIST) return true;
        if (keyCode == KEY_MEDIA_PLAY_PAUSE || keyCode == KEY_MEDIA_NEXT || keyCode == KEY_MEDIA_PREVIOUS) {
            return doublePress && screenOn;
        }
        return false;
    }

    /** Two presses close enough together to have been meant as one gesture. */
    public static boolean doublePress(long firstMs, long secondMs) {
        if (firstMs <= 0 || secondMs < firstMs) return false;
        return secondMs - firstMs <= DOUBLE_PRESS_MS;
    }

    /** The assistant role can be held from Android 10; older phones use the widget. */
    public static boolean canHoldAssistantRole(int sdkInt) {
        return sdkInt >= ROLE_SDK;
    }

    /**
     * Whether to offer "Make Jarvis the assistant" in settings: only where the
     * role exists, only while someone else holds it, and never once it is ours.
     */
    public static boolean offerAssistantRole(int sdkInt, boolean alreadyOurs) {
        return canHoldAssistantRole(sdkInt) && !alreadyOurs;
    }

    /** One line telling a person which way in this phone actually has. */
    public static String entrySummary(boolean widgetPlaced, boolean roleHeld, boolean buttonSeen) {
        if (buttonSeen) return "Press your headset button, or use the Jarvis widget.";
        if (roleHeld) return "Hold your home button, or use the Jarvis widget.";
        if (widgetPlaced) return "Tap the Jarvis widget to talk.";
        return "Add the Jarvis widget to your home screen, or hold the home button.";
    }
}
