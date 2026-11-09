package com.jarvisos.app.widgets;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * The Bible hub's two "pick up" tiles, from /api/bible/state and
 * /api/bible/plans. Pure, so it is tested on the JVM.
 */
public final class BibleHub {

    private BibleHub() {}

    /** A tile's value, caption and the reference a tap opens (null: just open the page). */
    public static final class Continue {
        public final String value;
        public final String caption;
        public final String ref;

        Continue(String value, String caption, String ref) {
            this.value = value;
            this.caption = caption;
            this.ref = ref;
        }
    }

    public static final class Plan {
        public final String value;
        public final String caption;
        public final String ref;

        Plan(String value, String caption, String ref) {
            this.value = value;
            this.caption = caption;
            this.ref = ref;
        }
    }

    /** Where reading left off: "John 3" and a tap that opens it. */
    public static Continue continueReading(JSONObject state) {
        JSONObject pos = state == null ? null : state.optJSONObject("position");
        if (pos == null) return new Continue("Start reading", "Open the Bible", null);
        String label = EntityNames.text(pos, "label");
        String book = EntityNames.text(pos, "book");
        int chapter = pos.optInt("chapter", 0);
        if (label.isEmpty()) label = chapter > 0 ? book + " " + chapter : book;
        if (label.isEmpty()) return new Continue("Start reading", "Open the Bible", null);
        int verse = pos.optInt("verse", 0);
        String ref = verse > 1 && chapter > 0 ? label + ":" + verse : label;
        return new Continue(label, verse > 1 ? "Verse " + verse + " · pick up here" : "Pick up where you left off", ref);
    }

    /** Today's reading in the first active plan: "Psalm 23" with "Day 12 of 90". */
    public static Plan todaysPlan(JSONObject plans) {
        JSONArray mine = plans == null ? null : plans.optJSONArray("mine");
        if (mine == null || mine.length() == 0) return new Plan("Choose a plan", "Reading plans", null);
        JSONObject plan = mine.optJSONObject(0);
        if (plan == null) return new Plan("Choose a plan", "Reading plans", null);
        String name = EntityNames.text(plan, "name");
        if (plan.optBoolean("finished", false)) return new Plan("Plan finished!", name, null);
        JSONObject today = plan.optJSONObject("today");
        JSONArray readings = today == null ? null : today.optJSONArray("readings");
        if (readings == null || readings.length() == 0) return new Plan("All caught up", name, null);
        String first = EntityNames.text(readings.optJSONObject(0), "ref");
        String value = readings.length() > 1 ? first + " +" + (readings.length() - 1) : first;
        int day = today.optInt("day", 0);
        int days = plan.optInt("days", 0);
        int behind = plan.optInt("behind", 0);
        String caption = (day > 0 && days > 0 ? "Day " + day + " of " + days : name)
                + (behind > 0 ? " · " + behind + " behind" : "");
        return new Plan(value, caption, first.isEmpty() ? null : first);
    }
}
