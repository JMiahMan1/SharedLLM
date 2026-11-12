package com.jarvisos.app;

/**
 * The phone's charging state and next alarm as Home Assistant's companion app
 * reports them, so the hub's {@code sensor.<phone>_battery_state},
 * {@code _charger_type}, {@code binary_sensor.<phone>_is_charging} and
 * {@code sensor.<phone>_next_alarm} read the same as they did when the
 * companion app was every phone's sensor source.
 *
 * And when a change is worth a report of its own: plugging in or unplugging
 * the charger, or a new next alarm, sends one promptly (automations trigger on
 * them), but never more often than {@link #MIN_GAP_MS}, so a loose cable
 * flapping on and off cannot turn into a stream of uploads.
 *
 * Plain Java with no Android types, so every case is unit-tested on the JVM;
 * LocationTrackingService feeds it the values from {@code ACTION_BATTERY_CHANGED}
 * and {@code AlarmManager.getNextAlarmClock()}.
 */
public final class PhoneStatusRules {

    private PhoneStatusRules() {}

    // android.os.BatteryManager's values, mirrored so this class has no Android types.
    static final int STATUS_UNKNOWN = 1;
    static final int STATUS_CHARGING = 2;
    static final int STATUS_DISCHARGING = 3;
    static final int STATUS_NOT_CHARGING = 4;
    static final int STATUS_FULL = 5;
    static final int PLUGGED_AC = 1;
    static final int PLUGGED_USB = 2;
    static final int PLUGGED_WIRELESS = 4;
    /** BatteryManager.BATTERY_PLUGGED_DOCK, API 33. */
    static final int PLUGGED_DOCK = 8;

    /** The fewest milliseconds between two reports a status change sends on its own. */
    public static final long MIN_GAP_MS = 30_000L;

    /**
     * {@code EXTRA_STATUS} as the companion app's battery state: charging,
     * discharging, full, not_charging, or unknown (also for -1, no reading).
     */
    public static String batteryState(int status) {
        switch (status) {
            case STATUS_CHARGING:
                return "charging";
            case STATUS_DISCHARGING:
                return "discharging";
            case STATUS_FULL:
                return "full";
            case STATUS_NOT_CHARGING:
                return "not_charging";
            default:
                return "unknown";
        }
    }

    /**
     * {@code EXTRA_PLUGGED} as the companion app's charger type: ac, usb,
     * wireless, dock or none. A value of -1 (no reading) is unknown.
     */
    public static String chargerType(int plugged) {
        if (plugged < 0) return "unknown";
        if ((plugged & PLUGGED_AC) != 0) return "ac";
        if ((plugged & PLUGGED_USB) != 0) return "usb";
        if ((plugged & PLUGGED_WIRELESS) != 0) return "wireless";
        if ((plugged & PLUGGED_DOCK) != 0) return "dock";
        return "none";
    }

    /** The companion app's is_charging: charging, or full while still on the charger. */
    public static boolean isCharging(int status) {
        return status == STATUS_CHARGING || status == STATUS_FULL;
    }

    /**
     * The next alarm to report, in milliseconds since the epoch: 0 when none is
     * set, and also for one already in the past (an alarm clock that has rung
     * but not yet been cleared), which is not a "next" alarm.
     */
    public static long nextAlarmMs(long triggerMs, long nowMs) {
        return triggerMs > nowMs ? triggerMs : 0L;
    }

    /** The app that set the alarm, or null when Android does not say. */
    public static String alarmPackage(String creatorPackage) {
        if (creatorPackage == null) return null;
        String p = creatorPackage.trim();
        return p.isEmpty() ? null : p;
    }

    /**
     * Rate limit for the reports a status change sends: the first goes at
     * once, changes inside {@link #MIN_GAP_MS} of the last report are folded
     * into one report at the end of the gap, so the last state always reaches
     * the server.
     */
    public static final class Limiter {
        /** Not scheduled: a report is already waiting for the end of the gap. */
        public static final long ALREADY_PENDING = -1L;

        private final long gapMs;
        private long lastAt;
        private boolean sentOnce;
        private boolean pending;

        public Limiter(long gapMs) {
            this.gapMs = gapMs;
        }

        public Limiter() {
            this(MIN_GAP_MS);
        }

        /**
         * A change wants a report at {@code nowMs}: 0 means send it now, a
         * positive value means send it that many milliseconds from now, and
         * {@link #ALREADY_PENDING} means one is already waiting, which will
         * carry this change too.
         */
        public synchronized long request(long nowMs) {
            if (pending) return ALREADY_PENDING;
            pending = true;
            if (!sentOnce || nowMs - lastAt >= gapMs) return 0L;
            // The clock moved back: no reason to wait longer than the gap.
            return Math.min(gapMs, lastAt + gapMs - nowMs);
        }

        /** The report was sent (or given up on) at {@code nowMs}. */
        public synchronized void sent(long nowMs) {
            lastAt = nowMs;
            sentOnce = true;
            pending = false;
        }
    }
}
