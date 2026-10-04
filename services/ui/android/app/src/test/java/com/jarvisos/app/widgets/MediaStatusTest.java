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
}