package com.jarvisos.app;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;

/**
 * Jarvis opened from the screen-time lock ("Open Jarvis" on the guard's lock
 * screen): when Jarvis may show over Android's lock screen, when the child has
 * left it, and what the web app is told.
 *
 * While a lock is in force the guard covers every app but the always-allowed
 * ones. Jarvis is one of them, but tapping "Open Jarvis" used to start it
 * behind Android's keyguard (the guard locks the phone itself when a lock
 * starts), so nothing visible happened. Now Jarvis shows over the keyguard for
 * as long as this "Jarvis mode" lasts, without ever dismissing it: the rest of
 * the phone stays locked, and the web app shows only the parts a parent left
 * open (the {@code locked_sections} of the guard's status).
 *
 * Plain Java with no Android types, so every case is unit-tested on the JVM;
 * GuardService and MainActivity feed it the state.
 */
public final class LockedJarvisRules {

    private LockedJarvisRules() {}

    /** A screen-time lock is in force: a protected phone, locked, outside a parent's unlock window. */
    public static boolean lockInForce(boolean protectedPhone, boolean locked, boolean unlocked) {
        return protectedPhone && locked && !unlocked;
    }

    /**
     * Jarvis mode: the child tapped "Open Jarvis" on the lock and the lock is
     * still in force. It ends the moment the lock does (a parent unlocked, more
     * time was given) or the child leaves Jarvis.
     */
    public static boolean active(boolean openedFromLock, boolean protectedPhone, boolean locked, boolean unlocked) {
        return openedFromLock && lockInForce(protectedPhone, locked, unlocked);
    }

    /**
     * Whether the window now in front means the child left Jarvis: another
     * app or the home screen. The guard's own screens, the system UI and the
     * call screens are not leaving (an incoming call over Jarvis comes back to
     * it), and an empty front (Android's keyguard over everything) is not
     * either. For {@code graceMs} after "Open Jarvis" the app that was locked
     * can still be focused while Jarvis starts, so it does not count yet.
     */
    public static boolean leftJarvis(String front, long msSinceOpened, long graceMs) {
        if (front == null || front.isEmpty()) return false;
        if (GuardRules.NEVER_LOCKED.contains(front)) return false;
        return msSinceOpened > graceMs;
    }

    /**
     * Whether MainActivity shows over Android's lock screen: for a Drop In call
     * that arrived while the phone was locked, or while Jarvis mode lasts.
     * Never otherwise, so an ordinary Jarvis is not readable on a locked phone.
     */
    public static boolean showWhenLocked(boolean dropInCall, boolean jarvisMode) {
        return dropInCall || jarvisMode;
    }

    /**
     * Whether MainActivity turns the screen on when it comes forward: only for
     * a Drop In call. Jarvis mode starts from a tap on a screen that is already
     * on, and turning it on again later (after it timed out) would wake a
     * locked phone for no one.
     */
    public static boolean turnScreenOn(boolean dropInCall) {
        return dropInCall;
    }

    /**
     * The sections the web app may show in Jarvis mode, as the server listed
     * them: blanks and repeats dropped, order kept. Which ids exist and which
     * are always on is the server's and the web app's business, not the
     * phone's, so an empty answer stays empty and the web app falls back to
     * chat alone.
     */
    public static List<String> sections(Collection<String> fromServer) {
        List<String> out = new ArrayList<>();
        if (fromServer == null) return out;
        for (String s : fromServer) {
            if (s == null) continue;
            String id = s.trim();
            if (!id.isEmpty() && !out.contains(id)) out.add(id);
        }
        return out;
    }
}
