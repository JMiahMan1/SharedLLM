package com.jarvisos.app.widgets;

import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

/**
 * Times as the widgets show them. The server sends ISO-8601: with an offset
 * or "Z" (an instant), without one (household local time, which is the
 * phone's zone at home), or a bare date (an all-day event). Pure.
 */
public final class WidgetTime {

    private WidgetTime() {}

    /** Epoch millis for an ISO date or date-time, or 0 when it cannot be read. */
    public static long parse(String iso, TimeZone local) {
        if (iso == null) return 0;
        String s = iso.trim();
        if (s.isEmpty() || "null".equals(s)) return 0;
        try {
            if (s.length() == 10) {
                SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
                f.setTimeZone(local);
                Date d = f.parse(s);
                return d == null ? 0 : d.getTime();
            }
            String base = s.length() >= 19 ? s.substring(0, 19).replace(' ', 'T') : s;
            String rest = s.length() > 19 ? s.substring(19) : "";
            // Drop fractional seconds, keep the zone.
            if (rest.startsWith(".")) {
                int i = 1;
                while (i < rest.length() && Character.isDigit(rest.charAt(i))) i++;
                rest = rest.substring(i);
            }
            SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US);
            if (rest.isEmpty()) {
                f.setTimeZone(local);
            } else if (rest.equals("Z")) {
                f.setTimeZone(TimeZone.getTimeZone("UTC"));
            } else {
                f.setTimeZone(TimeZone.getTimeZone("GMT" + rest));
            }
            Date d = f.parse(base);
            return d == null ? 0 : d.getTime();
        } catch (Exception e) {
            return 0;
        }
    }

    public static long parse(String iso) {
        return parse(iso, TimeZone.getDefault());
    }

    /** "9:15 PM". */
    public static String clock(long ms, TimeZone tz) {
        SimpleDateFormat f = new SimpleDateFormat("h:mm a", Locale.US);
        f.setTimeZone(tz);
        return f.format(new Date(ms));
    }

    public static String clock(long ms) {
        return clock(ms, TimeZone.getDefault());
    }

    /** "Today", "Tomorrow", or the weekday ("Tue") for later this week, else "Nov 12". */
    public static String day(long ms, long now, TimeZone tz) {
        Calendar a = Calendar.getInstance(tz, Locale.US);
        a.setTimeInMillis(now);
        Calendar b = Calendar.getInstance(tz, Locale.US);
        b.setTimeInMillis(ms);
        int diff = dayIndex(b) - dayIndex(a);
        if (diff == 0) return "Today";
        if (diff == 1) return "Tomorrow";
        if (diff == -1) return "Yesterday";
        SimpleDateFormat f = new SimpleDateFormat(diff > 1 && diff < 7 ? "EEE" : "MMM d", Locale.US);
        f.setTimeZone(tz);
        return f.format(new Date(ms));
    }

    private static int dayIndex(Calendar c) {
        return c.get(Calendar.YEAR) * 400 + c.get(Calendar.DAY_OF_YEAR);
    }

    /** "1h 20m", "45m", "0m". */
    public static String minutes(int min) {
        if (min < 0) min = 0;
        if (min < 60) return min + "m";
        int h = min / 60;
        int m = min % 60;
        return m == 0 ? h + "h" : h + "h " + m + "m";
    }
}
