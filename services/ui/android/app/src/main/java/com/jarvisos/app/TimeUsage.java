package com.jarvisos.app;

import com.jarvisos.app.widgets.ScreenTimeFormat;
import com.jarvisos.app.widgets.WidgetTime;

import org.json.JSONObject;

/**
 * How today's screen time reads where the child checks it (the Quick
 * Settings tile and the time panel): how much they may use today, how much
 * of it is gone, and where the extra came from. Reads the same status the
 * guard uses. Plain Java, JVM-tested.
 */
public final class TimeUsage {

    private TimeUsage() {}

    /** Everything allowed today: the limit plus bonus, earned and banked time. */
    public static int allowed(JSONObject s) {
        if (s == null) return 0;
        return Math.max(0, s.optInt("limit_min", 0)) + Math.max(0, s.optInt("bonus_min", 0))
                + Math.max(0, s.optInt("earned_min", 0)) + Math.max(0, s.optInt("banked_min", 0));
    }

    /** Percent of today's allowed time already used (0-100). */
    public static int usedPercent(JSONObject s) {
        int allowed = allowed(s);
        if (allowed <= 0) return s != null && s.optInt("used_min", 0) > 0 ? 100 : 0;
        int used = Math.max(0, s.optInt("used_min", 0));
        return Math.max(0, Math.min(100, Math.round(used * 100f / allowed)));
    }

    /** "Includes 20m earned and 15m from a parent", or "" when it is just the limit. */
    public static String extraLine(JSONObject s) {
        if (s == null || !ScreenTimeFormat.limited(s)) return "";
        StringBuilder out = new StringBuilder();
        append(out, s.optInt("earned_min", 0), "earned");
        append(out, s.optInt("bonus_min", 0), "from a parent");
        append(out, s.optInt("banked_min", 0), "saved from earlier");
        return out.length() == 0 ? "" : "Includes " + out;
    }

    private static void append(StringBuilder out, int minutes, String what) {
        if (minutes <= 0) return;
        if (out.length() > 0) out.append(", ");
        out.append(WidgetTime.minutes(minutes)).append(' ').append(what);
    }

    /** The tile's one line: "45m left", "Used 1h 15m", or the lock's reason. */
    public static String tileLine(JSONObject s) {
        if (s == null || s.length() == 0) return "Not available";
        if (ScreenTimeFormat.locked(s)) return ScreenTimeFormat.value(s);
        if (ScreenTimeFormat.limited(s)) return ScreenTimeFormat.value(s);
        return "Used " + WidgetTime.minutes(Math.max(0, s.optInt("used_min", 0)));
    }
}
