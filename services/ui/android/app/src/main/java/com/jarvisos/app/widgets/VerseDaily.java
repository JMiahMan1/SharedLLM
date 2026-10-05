package com.jarvisos.app.widgets;

import org.json.JSONObject;

/** Pure parsing of the /api/bible/daily payload into what the widget prints. */
public final class VerseDaily {

    public static final class Lines {
        public final String reference;
        public final String text;
        public final String sub;
        public final String streak;
        public final String warning;

        Lines(String reference, String text, String sub, String streak, String warning) {
            this.reference = reference;
            this.text = text;
            this.sub = sub;
            this.streak = streak;
            this.warning = warning;
        }
    }

    private static final String NO_TEXT = "—";

    private VerseDaily() {}

    /** Narrows any server message to the one line a widget can actually show. */
    public static String oneLine(String message) {
        if (message == null) return "offline";
        String flat = message.replaceAll("\\s+", " ").trim();
        if (flat.isEmpty()) return "offline";
        return DeviceButtonWidget.truncate(flat, 80);
    }

    public static Lines fromJson(JSONObject daily) {
        if (daily == null) return new Lines("", NO_TEXT, "No verse available today", "", "empty response");

        String streak = "";
        JSONObject streaks = daily.optJSONObject("streaks");
        if (streaks != null) {
            int current = streaks.optInt("read_streak_current", 0);
            streak = current > 0 ? current + " day streak" : "Start a streak today";
        }

        JSONObject verse = daily.optJSONObject("verse_of_day");
        if (verse == null) {
            return new Lines("", NO_TEXT, "No verse available today", streak, "no verse_of_day");
        }
        String verseError = verse.optString("error", "");
        if (!verseError.isEmpty()) {
            return new Lines("", NO_TEXT, oneLine(verseError), streak, verseError);
        }

        String reference = verse.optString("reference", "");
        String text = verse.optString("text", "");
        if (text.isEmpty()) {
            return new Lines(reference, NO_TEXT, "No verse available today", streak, "empty verse text");
        }

        JSONObject devotional = daily.optJSONObject("devotional");
        JSONObject entry = devotional == null ? null : devotional.optJSONObject("entry");
        if (entry != null) {
            String entryReference = entry.optString("reference", "");
            if (!entryReference.isEmpty()) {
                return new Lines(reference, text, "Devotional: " + entryReference, streak, "");
            }
            String title = entry.optString("title", "");
            if (!title.isEmpty()) {
                return new Lines(reference, text, title, streak, "");
            }
        }
        String reason = devotional == null ? "" : devotional.optString("reason", "");
        if (!reason.isEmpty()) {
            return new Lines(reference, text, oneLine(reason), streak, reason);
        }
        String version = verse.optString("version", "");
        return new Lines(reference, text, version, streak, "");
    }
}