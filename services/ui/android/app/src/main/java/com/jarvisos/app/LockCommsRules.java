package com.jarvisos.app;

import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * While a child's phone is locked, nothing paired with it may be a way around
 * the lock: not a smartwatch, not a car, not a messenger. Plain Java with no
 * Android types, so every case is unit-tested on the JVM; {@code
 * DeviceOwnerGuard} applies the suspension and {@code LockCallFilterService}
 * the call rule.
 *
 * Texting: every messaging app (the default SMS app too, and any a parent put
 * on the always-allowed list) and every watch companion app is suspended for
 * the lock. A suspended app cannot open, and Android hides its notifications
 * from the shade and from every notification listener, which is how a watch
 * mirrors them, so there is nothing on the wrist to read or reply to. Incoming
 * texts are still delivered and stored (measured on Android 17: a text sent
 * to a phone whose default SMS app was suspended was in the SMS store, with
 * its notification hidden, and the notification came back on unsuspend).
 * Bluetooth is never switched off.
 *
 * Calls: an outgoing call while locked goes through only to one of the
 * parents' numbers ({@code lock_call_allow}); Android never hands an
 * emergency call to a call redirection service, so 911 always works. A call
 * from a watch or a car goes through Telecom like one from the dialer, so the
 * same rule applies to it.
 */
public final class LockCommsRules {

    /**
     * Phone-side companion apps of the watches a child may wear. A watch's
     * notifications, replies and app sync all run through these.
     */
    static final Set<String> WATCH_COMPANIONS = Collections.unmodifiableSet(new LinkedHashSet<>(Arrays.asList(
            // Samsung Galaxy Wearable, its transport, and the per-model plugins.
            "com.samsung.android.app.watchmanager",
            "com.samsung.android.app.watchmanagerstub",
            "com.samsung.accessory",
            "com.samsung.android.gear1plugin",
            "com.samsung.android.gearoplugin",
            "com.samsung.android.gear2plugin",
            "com.samsung.android.gearpplugin",
            "com.samsung.android.gearrplugin",
            "com.samsung.android.geargplugin",
            "com.samsung.android.gearnplugin",
            "com.samsung.android.waterplugin",
            "com.samsung.android.heartplugin",
            "com.samsung.wearable.watch6plugin",
            "com.samsung.wearable.watch7plugin",
            "com.samsung.android.gearfit2plugin",
            "com.samsung.android.modenplugin",
            "com.samsung.android.neatplugin",
            "com.samsung.android.fit3plugin",
            // Wear OS: the Wear OS app and the Pixel Watch app.
            "com.google.android.wearable.app",
            "com.google.android.apps.wear.companion",
            // Fitbit.
            "com.fitbit.FitbitMobile",
            // Garmin Connect, and Garmin Messenger (texts from the watch).
            "com.garmin.android.apps.connectmobile",
            "com.garmin.android.apps.messenger")));

    /**
     * Package prefixes of companion apps that are new each year (Samsung's next
     * watch plugin), matched against the apps installed, so a new watch is
     * covered before this list names it.
     */
    static final Set<String> WATCH_COMPANION_PREFIXES = Collections.unmodifiableSet(new LinkedHashSet<>(Arrays.asList(
            "com.samsung.wearable.watch")));

    /** SMS/RCS apps and third-party messengers. */
    static final Set<String> MESSAGING_APPS = Collections.unmodifiableSet(new LinkedHashSet<>(Arrays.asList(
            // SMS and RCS.
            "com.google.android.apps.messaging",
            "com.android.mms",
            "com.android.messaging",
            "com.samsung.android.messaging",
            "com.motorola.messaging",
            "com.oneplus.mms",
            "com.sonyericsson.conversations",
            "com.verizon.messaging.vzmsgs",
            "com.textra",
            "com.p1.chompsms",
            "com.handcent.app.nextsms",
            "com.google.android.apps.googlevoice",
            "com.enflick.android.TextNow",
            // Messengers.
            "com.whatsapp",
            "com.whatsapp.w4b",
            "com.facebook.orca",
            "com.facebook.mlite",
            "com.facebook.talk",
            "org.telegram.messenger",
            "org.telegram.messenger.web",
            "org.thunderdog.challegram",
            "org.thoughtcrime.securesms",
            "com.snapchat.android",
            "com.discord",
            "com.instagram.android",
            "kik.android",
            "com.groupme.android",
            "com.google.android.apps.dynamite",
            "com.viber.voip",
            "jp.naver.line.android",
            "com.tencent.mm",
            "com.kakao.talk",
            "com.skype.raider",
            "com.microsoft.teams",
            "com.Slack",
            "ch.threema.app",
            "im.vector.app",
            "com.wire")));

    /**
     * Never suspended for the lock, whatever a list says: Jarvis (the lock
     * screen and family chat), the system UI, emergency and in-call screens,
     * and the dialers, so a locked phone can always call a parent or 911.
     */
    static final Set<String> KEEP = Collections.unmodifiableSet(new LinkedHashSet<>(Arrays.asList(
            GuardRules.OWN_PACKAGE,
            "com.android.systemui",
            "com.android.emergency",
            "com.android.phone",
            "com.android.incallui",
            "com.samsung.android.incallui",
            "com.android.server.telecom",
            "com.google.android.dialer",
            "com.android.dialer",
            "com.samsung.android.dialer",
            "com.android.providers.telephony",
            "com.android.mms.service")));

    /** Fewest digits a dialled number may have and still match a parent's by its tail. */
    static final int MIN_MATCH_DIGITS = 7;

    private LockCommsRules() {}

    /**
     * Whether the lock's call and watch rules are in force now: a protected
     * phone, locked, and not inside a parent's unlock window. Bedtime, school
     * time, a pause and the day's limit all lock the same way.
     */
    public static boolean active(boolean protectedPhone, boolean locked, boolean unlocked) {
        return protectedPhone && locked && !unlocked;
    }

    /**
     * Packages to suspend for the lock, on a device owner (only a device owner
     * may suspend apps): the watch companions, the messaging apps, the default
     * SMS app and the server's extra list ({@code lock_suspend_apps}), limited
     * to what is installed, and never anything in {@link #KEEP}, the default
     * dialer or Jarvis. Empty when the lock is not in force.
     *
     * @param installed every installed package; null when unknown, and then
     *                  nothing is matched by prefix and nothing is filtered out
     */
    public static Set<String> lockSuspensions(boolean deviceOwner, boolean protectedPhone, boolean locked,
                                              boolean unlocked, Collection<String> installed,
                                              Collection<String> serverExtra, String defaultSms,
                                              String defaultDialer, String jarvisPackage) {
        if (!deviceOwner || !active(protectedPhone, locked, unlocked)) return Collections.emptySet();
        Set<String> candidates = new LinkedHashSet<>(WATCH_COMPANIONS);
        candidates.addAll(MESSAGING_APPS);
        if (serverExtra != null) candidates.addAll(serverExtra);
        if (defaultSms != null) candidates.add(defaultSms);
        if (installed != null) {
            for (String pkg : installed) {
                if (pkg == null) continue;
                for (String prefix : WATCH_COMPANION_PREFIXES) {
                    if (pkg.startsWith(prefix)) candidates.add(pkg);
                }
            }
        }
        Set<String> out = new LinkedHashSet<>();
        for (String pkg : candidates) {
            if (pkg == null || pkg.isEmpty()) continue;
            if (KEEP.contains(pkg) || pkg.equals(defaultDialer) || pkg.equals(jarvisPackage)) continue;
            if (installed != null && !installed.contains(pkg)) continue;
            out.add(pkg);
        }
        return out;
    }

    /**
     * Whether an outgoing call may go through. Outside the lock, always. While
     * locked, only to a number in {@code allowed} (the parents', E.164), or one
     * the platform says is an emergency number. Android does not send
     * emergency calls to a call redirection service at all; {@code emergency}
     * is the second line of defence, never the only one.
     */
    public static boolean callAllowed(boolean lockActive, String dialled, Collection<String> allowed, boolean emergency) {
        if (!lockActive || emergency) return true;
        if (dialled == null || allowed == null) return false;
        for (String number : allowed) {
            if (sameNumber(dialled, number)) return true;
        }
        return false;
    }

    /**
     * Whether a dialled number reaches {@code allowedE164}. Formatting is
     * ignored. A number dialled with its country code ({@code +} or the
     * {@code 00}/{@code 011} international prefix) must match exactly; one
     * dialled without it (national form, a trunk {@code 0} dropped) must be
     * the tail of the allowed number and at least {@link #MIN_MATCH_DIGITS}
     * long, so a short code or a fragment never matches a parent. A dialled
     * string with anything but digits and formatting (a {@code *67} or
     * {@code #31#} prefix, pauses, letters) does not match: refusing is the
     * safe answer.
     */
    public static boolean sameNumber(String dialled, String allowedE164) {
        String allowed = digitsOrNull(allowedE164 == null ? null : allowedE164.trim().replaceFirst("^\\+", ""));
        if (allowed == null || allowed.length() < MIN_MATCH_DIGITS) return false;
        String raw = dialled == null ? "" : dialled.trim();
        boolean plus = raw.startsWith("+");
        String digits = digitsOrNull(plus ? raw.substring(1) : raw);
        if (digits == null || digits.isEmpty()) return false;
        if (plus) return digits.equals(allowed);
        if (digits.startsWith("011") && digits.substring(3).equals(allowed)) return true;
        if (digits.startsWith("00") && digits.substring(2).equals(allowed)) return true;
        String national = digits.replaceFirst("^0+", "");
        return national.length() >= MIN_MATCH_DIGITS && allowed.endsWith(national);
    }

    /** The digits of a number written with ordinary formatting, or null when it holds anything else. */
    private static String digitsOrNull(String s) {
        if (s == null) return null;
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c >= '0' && c <= '9') {
                out.append(c);
            } else if (c != ' ' && c != '-' && c != '.' && c != '(' && c != ')') {
                return null;
            }
        }
        return out.toString();
    }

    /**
     * Whether to take every outgoing call away for the lock instead
     * ({@code DISALLOW_OUTGOING_CALLS}; Android still places emergency calls):
     * the fail-safe for a device owner whose call filter role was never
     * granted, so a locked phone without it cannot call anyone else. It also
     * stops calls to the parents until the role is granted, which the parents'
     * device status says.
     */
    public static boolean blockAllOutgoing(boolean deviceOwner, boolean protectedPhone, boolean locked,
                                           boolean unlocked, boolean callFilterHeld) {
        return deviceOwner && active(protectedPhone, locked, unlocked) && !callFilterHeld;
    }

    /**
     * Lock-task allowlist while the phone is pinned to the guard screen:
     * Jarvis, the default dialer (the lock's "Call a parent" opens it) and the
     * in-call and emergency screens, so a call to a parent or 911 can be
     * placed and seen. Not the system UI: lock task needs no allowlisting for
     * it, and listing it would let its own activities run.
     */
    public static Set<String> lockTaskPackages(String jarvisPackage, String defaultDialer) {
        Set<String> out = new LinkedHashSet<>();
        if (jarvisPackage != null && !jarvisPackage.isEmpty()) out.add(jarvisPackage);
        if (defaultDialer != null && !defaultDialer.isEmpty()) out.add(defaultDialer);
        for (String pkg : GuardRules.NEVER_LOCKED) {
            if (!"com.android.systemui".equals(pkg)) out.add(pkg);
        }
        return out;
    }
}
