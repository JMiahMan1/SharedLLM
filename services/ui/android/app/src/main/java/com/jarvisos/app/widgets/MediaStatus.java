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
 * It also read fields with {@code optString}, which on a phone prints the
 * handler's JSON {@code null}s as the word "null" (see {@link EntityNames#text}).
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

        // EntityNames.text, never optString: on a phone optString turns the
        // handler's JSON nulls into the word "null".
        String state = EntityNames.text(player, "state");

        String title = EntityNames.text(player, "media_title");
        if (title.isEmpty()) title = EntityNames.text(player, "source");
        if (title.isEmpty()) title = EntityNames.friendlyName(player);
        if (title.isEmpty()) title = "Nothing playing";

        String artist = EntityNames.text(player, "media_artist");

        return new Lines(title, artist.isEmpty() ? state : artist, "playing".equalsIgnoreCase(state));
    }
}