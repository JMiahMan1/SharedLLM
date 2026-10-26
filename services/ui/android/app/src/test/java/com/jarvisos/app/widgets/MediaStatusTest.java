package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

/**
 * Desktop-JVM tests for what the media home-screen widget prints.
 *
 * <p>The payload below is copied from the media status handler's own shape
 * ({@code detail.active}, {@code detail.available}, {@code detail.all_players}).
 * The widget used to read {@code detail.player}/{@code detail.entity}, which do
 * not exist, so every one of these cases printed "Nothing playing" no matter
 * what was playing.
 */
public class MediaStatusTest {

    /** A playing MA speaker, as media/status reports it. */
    private static JSONObject playing() throws Exception {
        return new JSONObject()
            .put("status", "SUCCESS")
            .put("message", "**Currently Playing:**")
            .put("service", "media")
            .put("detail", new JSONObject()
                .put("active", new JSONObject()
                    .put("entity_id", "media_player.kitchen")
                    .put("friendly_name", "Kitchen Speaker")
                    .put("state", "playing")
                    .put("media_title", "The Way of Kings")
                    .put("media_artist", "Brandon Sanderson")
                    .put("source", "library")
                    .put("volume_level", 0.42))
                .put("available", new JSONObject())
                .put("all_players", new JSONObject()
                    .put("0", new JSONObject().put("entity_id", "media_player.kitchen"))));
    }

    @Test
    public void printsTheTrackThatIsPlaying() throws Exception {
        MediaStatus.Lines lines = MediaStatus.fromJson(playing());
        assertEquals("The Way of Kings", lines.title);
        assertEquals("Brandon Sanderson", lines.subtitle);
        assertTrue(lines.playing);
        // The transport buttons target the player the widget is showing.
        assertEquals("media_player.kitchen", lines.entityId);
    }

    @Test
    public void transportCommandsMatchTheExecutionRoutesVocabulary() throws Exception {
        // /execute/media/transport rejects play_pause/next_track/previous_track.
        assertEquals("pause", MediaStatus.transportCommand("play_pause", true));
        assertEquals("resume", MediaStatus.transportCommand("play_pause", false));
        assertEquals("next", MediaStatus.transportCommand("next_track", true));
        assertEquals("previous", MediaStatus.transportCommand("previous_track", false));
        // Unknown buttons pass through unchanged rather than inventing a command.
        assertEquals("stop", MediaStatus.transportCommand("stop", true));
    }

    @Test
    public void noActivePlayerMeansNoEntityToTarget() throws Exception {
        assertEquals("", MediaStatus.fromJson(new JSONObject().put("detail", new JSONObject())).entityId);
        assertEquals("", MediaStatus.fromJson(null).entityId);
    }

    @Test
    public void readsTheWebPlayerInjectedForLocalPlayback() throws Exception {
        // media_playback_service overrides detail.active with a synthetic player
        // when the user's chosen target is the local web player.
        JSONObject local = new JSONObject()
            .put("detail", new JSONObject()
                .put("active", new JSONObject()
                    .put("entity_id", "web_player")
                    .put("friendly_name", "Web Player")
                    .put("state", "paused")
                    .put("media_title", "Unknown Title")
                    .put("media_artist", "Unknown Artist")));
        MediaStatus.Lines lines = MediaStatus.fromJson(local);
        assertEquals("Unknown Title", lines.title);
        assertEquals("Unknown Artist", lines.subtitle);
        assertFalse(lines.playing);
    }

    @Test
    public void saysNothingPlayingWhenNoPlayerIsActive() throws Exception {
        JSONObject idle = new JSONObject()
            .put("detail", new JSONObject()
                .put("active", JSONObject.NULL)
                .put("available", new JSONObject()
                    .put("0", new JSONObject()
                        .put("entity_id", "media_player.living_room")
                        .put("friendly_name", "Living Room Speaker")
                        .put("state", "idle"))));
        MediaStatus.Lines lines = MediaStatus.fromJson(idle);
        assertEquals("Nothing playing", lines.title);
        assertEquals("", lines.subtitle);
        assertFalse(lines.playing);
    }

    @Test
    public void fallsBackToThePlayerNameWhenThereIsNoTrack() throws Exception {
        JSONObject radio = new JSONObject()
            .put("detail", new JSONObject()
                .put("active", new JSONObject()
                    .put("entity_id", "media_player.office")
                    .put("friendly_name", "Office Speaker")
                    .put("state", "playing")));
        MediaStatus.Lines lines = MediaStatus.fromJson(radio);
        assertEquals("Office Speaker", lines.title);
        // No artist, so the second line carries the state.
        assertEquals("playing", lines.subtitle);
    }

    @Test
    public void prefersTheSourceOverAPlayerNameForATuner() throws Exception {
        JSONObject radio = new JSONObject()
            .put("detail", new JSONObject()
                .put("active", new JSONObject()
                    .put("entity_id", "media_player.office_tv")
                    .put("friendly_name", "Office TV")
                    .put("state", "playing")
                    .put("source", "Live TV")));
        assertEquals("Live TV", MediaStatus.fromJson(radio).title);
    }

    @Test
    public void survivesAPayloadWithNoDetailAtAll() throws Exception {
        // A gateway or execution failure that still answers 200.
        MediaStatus.Lines lines = MediaStatus.fromJson(new JSONObject().put("status", "FAILURE"));
        assertEquals("Nothing playing", lines.title);
        assertFalse(lines.playing);
        assertEquals("Nothing playing", MediaStatus.fromJson(null).title);
    }

    /**
     * A JSONObject that reads strings the way Android's org.json does: a JSON
     * null comes back from {@code optString} as the string "null", not the
     * fallback. The desktop org.json these tests run on does the opposite, which
     * is how the widget shipped printing "null" without a test noticing.
     */
    private static final class AndroidJson extends JSONObject {
        @Override
        public String optString(String key, String fallback) {
            Object value = opt(key);
            return value == null ? fallback : String.valueOf(value);
        }

        @Override
        public String optString(String key) {
            return optString(key, "");
        }
    }

    /** An idle speaker as the handler sends it: every unreported attribute is null. */
    private static JSONObject idleTarget() throws Exception {
        JSONObject player = new AndroidJson()
            .put("entity_id", "media_player.kitchen")
            .put("friendly_name", "Kitchen Speaker")
            .put("state", "idle")
            .put("media_title", JSONObject.NULL)
            .put("media_artist", JSONObject.NULL)
            .put("media_album", JSONObject.NULL)
            .put("source", JSONObject.NULL)
            .put("entity_picture", JSONObject.NULL);
        return new JSONObject().put("detail", new JSONObject().put("active", player));
    }

    @Test
    public void theAndroidStandInReallyPrintsNullForOptString() throws Exception {
        // Guards the stand-in itself, so the next test proves something.
        JSONObject player = idleTarget().getJSONObject("detail").getJSONObject("active");
        assertEquals("null", player.optString("media_title", null));
    }

    @Test
    public void neverPrintsTheWordNullForAttributesThePlayerDoesNotReport() throws Exception {
        // media_playback_service makes the user's chosen speaker detail.active
        // even when it is idle, with media_title/media_artist/source all null.
        MediaStatus.Lines lines = MediaStatus.fromJson(idleTarget());
        assertEquals("Kitchen Speaker", lines.title);
        assertEquals("idle", lines.subtitle);
        assertFalse(lines.playing);
    }

    @Test
    public void namesAPlayerFromItsIdWhenTheFriendlyNameIsNull() throws Exception {
        JSONObject player = new AndroidJson()
            .put("entity_id", "media_player.living_room_tv")
            .put("friendly_name", JSONObject.NULL)
            .put("state", "playing")
            .put("media_title", JSONObject.NULL)
            .put("media_artist", JSONObject.NULL)
            .put("source", JSONObject.NULL);
        MediaStatus.Lines lines = MediaStatus.fromJson(new JSONObject().put("detail", new JSONObject().put("active", player)));
        assertEquals("living room tv", lines.title);
        assertEquals("playing", lines.subtitle);
        assertTrue(lines.playing);
    }

    @Test
    public void textTreatsMissingAndNullAlikeAndTrims() throws Exception {
        JSONObject o = new AndroidJson().put("a", JSONObject.NULL).put("b", "  Kitchen  ").put("c", 3);
        assertEquals("", EntityNames.text(o, "a"));
        assertEquals("", EntityNames.text(o, "missing"));
        assertEquals("Kitchen", EntityNames.text(o, "b"));
        assertEquals("3", EntityNames.text(o, "c"));
        assertEquals("", EntityNames.text(null, "a"));
    }
}