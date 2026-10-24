package com.jarvisos.app.widgets;

import java.text.NumberFormat;
import java.util.Locale;
import org.json.JSONObject;

/**
 * What the Health today widget prints, from the same two reads the app's
 * Today tab uses: {@code /api/health/summary} and {@code /api/geo/steps?days=1}.
 * Free of Android types so each state is JVM-tested.
 *
 * Nothing is invented: a missing reading prints a dash and says why ("Not
 * logged", "Check in"), never a zero. Calories are not shown at all, so a
 * child's home screen never carries a calorie number.
 */
public final class HealthTodayFormat {

    /** One tile: the big value and the quiet line under it. */
    public static final class Tile {
        public final String value;
        public final String caption;

        Tile(String value, String caption) {
            this.value = value;
            this.caption = caption;
        }
    }

    public static final String DASH = "–";
    private static final double ML_PER_FL_OZ = 29.5735;

    private HealthTodayFormat() {}

    private static String number(long value) {
        return NumberFormat.getIntegerInstance(Locale.US).format(value);
    }

    /** Steps today against the goal; {@code steps} null means the read failed. */
    public static Tile steps(JSONObject steps) {
        if (steps == null) return new Tile(DASH, "Unavailable");
        JSONObject daily = steps.optJSONObject("daily_steps");
        if (daily == null || daily.length() == 0) return new Tile(DASH, "No steps yet");
        int today = Math.max(0, steps.optInt("today", 0));
        int goal = steps.optInt("goal", 0);
        return new Tile(number(today), goal > 0 ? "of " + number(goal) : "steps");
    }

    /** Readiness score and its own label, or how to get one. */
    public static Tile readiness(JSONObject summary) {
        if (summary == null) return new Tile(DASH, "Unavailable");
        JSONObject ready = summary.optJSONObject("readiness");
        if (ready == null || ready.isNull("score")) return new Tile(DASH, "Check in");
        String label = ready.optString("label", "");
        return new Tile(String.valueOf(ready.optInt("score")), label.isEmpty() ? "readiness" : label);
    }

    /** "7h 05m" for a duration in minutes. */
    public static String hoursMinutes(int minutes) {
        if (minutes < 60) return minutes + "m";
        return (minutes / 60) + "h " + String.format(Locale.US, "%02d", minutes % 60) + "m";
    }

    /** Last night's sleep score, with how long. */
    public static Tile sleep(JSONObject summary) {
        if (summary == null) return new Tile(DASH, "Unavailable");
        JSONObject sleep = summary.optJSONObject("sleep");
        JSONObject score = sleep == null ? null : sleep.optJSONObject("score");
        JSONObject night = sleep == null ? null : sleep.optJSONObject("last_night");
        if (score == null || score.isNull("score")) {
            return new Tile(DASH, night == null ? "Not logged" : hoursMinutes(night.optInt("duration_minutes")));
        }
        String caption = night != null && night.optInt("duration_minutes", 0) > 0
            ? hoursMinutes(night.optInt("duration_minutes"))
            : score.optString("label", "sleep");
        return new Tile(String.valueOf(score.optInt("score")), caption);
    }

    /** Water so far in the person's units (imperial → fl oz, else litres). */
    public static Tile water(JSONObject summary) {
        if (summary == null) return new Tile(DASH, "Unavailable");
        JSONObject water = summary.optJSONObject("water");
        if (water == null || !water.optBoolean("logged", false)) return new Tile(DASH, "None logged");
        JSONObject profile = summary.optJSONObject("profile");
        boolean imperial = profile != null && "imperial".equals(profile.optString("units"));
        double ml = water.optDouble("ml", 0);
        double goal = water.isNull("goal_ml") ? 0 : water.optDouble("goal_ml", 0);
        if (imperial) {
            String value = Math.round(ml / ML_PER_FL_OZ) + " oz";
            return new Tile(value, goal > 0 ? "of " + Math.round(goal / ML_PER_FL_OZ) + " oz" : "water");
        }
        String value = String.format(Locale.US, "%.1f L", ml / 1000.0);
        return new Tile(value, goal > 0 ? String.format(Locale.US, "of %.1f L", goal / 1000.0) : "water");
    }
}
