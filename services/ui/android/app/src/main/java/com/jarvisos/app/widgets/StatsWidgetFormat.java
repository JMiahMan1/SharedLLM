package com.jarvisos.app.widgets;

import java.text.NumberFormat;
import java.util.Locale;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * What the Family Games and Bible widgets print, from the same reads the app
 * makes (/api/arcade/scoreboard, /api/bible/streaks, the daily-quiz
 * leaderboard and the memory verses due). Pure, so it is tested on the JVM.
 * A dash means "could not read it", never zero.
 */
public final class StatsWidgetFormat {

    private StatsWidgetFormat() {}

    /** One tile: the big value and the small line under it. */
    public static final class Tile {
        public final String value;
        public final String caption;

        Tile(String value, String caption) {
            this.value = value;
            this.caption = caption;
        }
    }

    static final Tile UNKNOWN = new Tile("-", "Unavailable");

    static String count(long n) {
        return NumberFormat.getIntegerInstance(Locale.US).format(n);
    }

    // --- Family Games (the signed-in member's arcade player card) ---

    /** The signed-in member's row on the family scoreboard, or null. */
    public static JSONObject me(JSONObject scoreboard) {
        if (scoreboard == null || !scoreboard.optBoolean("success", false)) return null;
        JSONArray players = scoreboard.optJSONArray("players");
        if (players == null) return null;
        for (int i = 0; i < players.length(); i++) {
            JSONObject p = players.optJSONObject(i);
            if (p != null && p.optBoolean("you", false)) return p;
        }
        return null;
    }

    public static Tile played(JSONObject me) {
        if (me == null) return UNKNOWN;
        int n = me.optInt("submits", 0);
        return new Tile(count(n), n == 0 ? "Play one!" : (n == 1 ? "game played" : "games played"));
    }

    public static Tile best(JSONObject me) {
        if (me == null) return UNKNOWN;
        int best = me.optInt("best_score", 0);
        if (best <= 0) return new Tile("-", "No score yet");
        String title = "";
        int top = -1;
        JSONArray games = me.optJSONArray("per_game");
        if (games != null) {
            for (int i = 0; i < games.length(); i++) {
                JSONObject g = games.optJSONObject(i);
                if (g == null) continue;
                int mine = g.optInt("my_best", 0);
                if (mine > top) {
                    top = mine;
                    title = g.optString("title", "");
                }
            }
        }
        return new Tile(count(best), title.isEmpty() ? "best score" : title);
    }

    public static Tile crowns(JSONObject me) {
        if (me == null) return UNKNOWN;
        int crowns = me.optInt("crowns", 0);
        int podiums = me.optInt("podiums", 0);
        if (crowns > 0) return new Tile(count(crowns), crowns == 1 ? "#1 spot" : "#1 spots");
        if (podiums > 0) return new Tile(count(podiums), "top-3 spots");
        return new Tile("0", "#1 spots");
    }

    /**
     * A caption for a narrow tile, where it wraps to two lines: a word longer
     * than 7 letters ("Jubilee!") would break mid-word, so it gives way to
     * {@code fallback}, which must wrap cleanly.
     */
    public static String narrowCaption(String caption, String fallback) {
        if (caption == null) return fallback;
        for (String word : caption.trim().split("\\s+")) {
            if (word.length() > 7) return fallback;
        }
        return caption;
    }

    // --- Bible (the signed-in reader's own streak, quiz and verses) ---

    public static Tile streak(JSONObject streaks) {
        if (streaks == null || !streaks.has("read_streak_current")) return UNKNOWN;
        int now = streaks.optInt("read_streak_current", 0);
        int longest = streaks.optInt("read_streak_longest", 0);
        String caption = "day streak";
        if (now == 0) caption = longest > 0 ? "best " + count(longest) + " days" : "Read today";
        return new Tile(count(now), caption);
    }

    public static Tile quiz(JSONObject leaderboard) {
        if (leaderboard == null || !leaderboard.has("entries")) return UNKNOWN;
        JSONArray entries = leaderboard.optJSONArray("entries");
        if (entries != null) {
            for (int i = 0; i < entries.length(); i++) {
                JSONObject e = entries.optJSONObject(i);
                if (e != null && e.optBoolean("you", false)) {
                    return new Tile(e.optInt("correct", 0) + "/" + e.optInt("total", 0), "today's quiz");
                }
            }
        }
        return new Tile("Play", "today's quiz");
    }

    public static Tile due(JSONObject due) {
        if (due == null || !due.has("count")) return UNKNOWN;
        int n = due.optInt("count", 0);
        if (n == 0) return new Tile("0", "all caught up");
        return new Tile(count(n), n == 1 ? "verse to review" : "verses to review");
    }
}
