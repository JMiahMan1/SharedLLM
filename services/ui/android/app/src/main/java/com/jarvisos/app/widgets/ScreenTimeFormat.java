package com.jarvisos.app.widgets;

import java.util.TimeZone;
import org.json.JSONObject;

/**
 * How a screen-time status (/api/screen-time/{user}/status, the same answer
 * the phone's guard reads) reads on the widget. Pure.
 */
public final class ScreenTimeFormat {

    private ScreenTimeFormat() {}

    static String reasonTitle(String reason) {
        switch (reason == null ? "" : reason) {
            case "paused": return "Paused by a parent";
            case "bedtime": return "It's bedtime";
            case "school": return "School time";
            case "chores": return "Chores first";
            case "limit": return "Time's up for today";
            default: return "Locked";
        }
    }

    static String nextName(String reason) {
        switch (reason == null ? "" : reason) {
            case "bedtime": return "Bedtime";
            case "school": return "School";
            case "limit": return "Time runs out";
            default: return "Locks";
        }
    }

    public static boolean locked(JSONObject s) {
        return s != null && s.optBoolean("locked", false);
    }

    public static boolean limited(JSONObject s) {
        return s != null && !s.isNull("limit_min") && s.optInt("limit_min", 0) > 0;
    }

    /** The big number: time left, or a word when there is no clock to show. */
    public static String left(JSONObject s) {
        if (s == null) return "-";
        if (locked(s)) return "0m";
        if (!limited(s)) return "∞";
        return WidgetTime.minutes(s.optInt("remaining_min", 0));
    }

    /** Percent of today's time still left (for the ring). */
    public static int percentLeft(JSONObject s) {
        if (s == null || locked(s)) return 0;
        if (!limited(s)) return 100;
        int total = s.optInt("limit_min", 0) + s.optInt("bonus_min", 0) + s.optInt("earned_min", 0)
                + s.optInt("banked_min", 0);
        int left = s.optInt("remaining_min", 0);
        if (total <= 0) return 0;
        return Math.max(0, Math.min(100, Math.round(left * 100f / total)));
    }

    public static String headline(JSONObject s) {
        if (s == null) return "Screen time unavailable";
        if (locked(s)) return reasonTitle(EntityNames.text(s, "reason"));
        if (!limited(s)) return "No time limit today";
        return WidgetTime.minutes(s.optInt("remaining_min", 0)) + " left today";
    }

    /** What happens next: "Until 7:00 AM" while locked, "Bedtime at 9:15 PM" otherwise. */
    public static String line1(JSONObject s, TimeZone tz) {
        if (s == null) return "";
        if (locked(s)) {
            long until = WidgetTime.parse(EntityNames.text(s, "until"), tz);
            return until > 0 ? "Until " + WidgetTime.clock(until, tz) : "Until a parent unlocks it";
        }
        long next = WidgetTime.parse(EntityNames.text(s, "next_lock_at"), tz);
        if (next > 0) return nextName(EntityNames.text(s, "next_lock_reason")) + " at " + WidgetTime.clock(next, tz);
        return "";
    }

    /** Chores that earn more time, else how much was used. */
    public static String line2(JSONObject s) {
        if (s == null) return "";
        int chores = s.optInt("chores_left", 0);
        if (chores > 0) return chores + (chores == 1 ? " chore to earn more" : " chores to earn more");
        int used = s.optInt("used_min", 0);
        return used > 0 ? "Used " + WidgetTime.minutes(used) + " today" : "";
    }

    /** A child's row for a parent: "1h 20m left", "Locked", "Paused". */
    public static String value(JSONObject s) {
        if (s == null) return "-";
        if (locked(s)) return "paused".equals(EntityNames.text(s, "reason")) ? "Paused" : "Locked";
        if (!limited(s)) return "No limit";
        return WidgetTime.minutes(s.optInt("remaining_min", 0)) + " left";
    }

    public static String status(JSONObject s, TimeZone tz) {
        if (s == null) return "Unavailable";
        if (locked(s)) {
            String l1 = line1(s, tz);
            return reasonTitle(EntityNames.text(s, "reason")) + (l1.startsWith("Until ") ? " · " + l1.toLowerCase(java.util.Locale.ROOT) : "");
        }
        String l1 = line1(s, tz);
        String used = "Used " + WidgetTime.minutes(s.optInt("used_min", 0));
        return l1.isEmpty() ? used : used + " · " + l1;
    }
}
