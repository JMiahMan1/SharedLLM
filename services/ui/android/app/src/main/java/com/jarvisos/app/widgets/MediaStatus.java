package com.jarvisos.app.widgets;

import org.json.JSONObject;

/**
 * What the media home-screen widget prints, derived from an
 * {@code /execute/media/status} response.
 *
 * <p>The payload shape is owned by the media status handler: {@code detail}
 * holds {@code active} (the now-playing player), {@code available} and
 * {@code all_players}, and every player object carries {@code entity_id},
 * {@code friendly_name}, {@code state} and the {@code media_*} attributes.
 *
 * <p>Kept apart from {@link MediaWidget}'s RemoteViews so it can be tested on a
 * plain JVM. That separation is what caught the bug it now fixes: the widget
 * looked for {@code detail.player} and {@code detail.entity}, neither of which
 * exists, so it read no player at all and always printed "Nothing playing".
 */
public final class MediaStatus {

    /** What the widget's two text lines and play/pause glyph need. */
    public static final class Lines {
        /** Track title, or the player name when there is no track. */
        public final String title;
        /** The artist when there is one, otherwise the player's state. */
        public final String subtitle;
        public final boolean playing;

        Lines(String title, String subtitle, boolean playing) {
            this.title = title;
            this.subtitle = subtitle;
            this.playing = playing;
        }
    }

    private MediaStatus() {}

    public static Lines fromJson(JSONObject response) {
        if (response == null) return new Lines("Nothing playing", "", false);

        // detail is the handler's payload; fall back to the envelope so a future
        // flattening of the response still renders instead of going blank.
        JSONObject detail = response.optJSONObject("detail");
        if (detail == null) detail = response;

        JSONObject player = detail.optJSONObject("active");
        if (player == null) return new Lines("Nothing playing", "", false);

        String state = orEmpty(player.optString("state", null));

        String title = orEmpty(player.optString("media_title", null));
        if (title.isEmpty()) title = orEmpty(player.optString("source", null));
        if (title.isEmpty()) title = EntityNames.friendlyName(player);
        if (title.isEmpty()) title = "Nothing playing";

        String artist = orEmpty(player.optString("media_artist", null));

        return new Lines(title, artist.isEmpty() ? state : artist, "playing".equalsIgnoreCase(state));
    }

    private static String orEmpty(String s) {
        return s == null ? "" : s.trim();
    }
}