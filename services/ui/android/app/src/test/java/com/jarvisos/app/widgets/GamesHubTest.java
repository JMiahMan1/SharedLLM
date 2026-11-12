package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

/** Desktop-JVM tests for the Family Games hub. */
public class GamesHubTest {

    private static JSONObject game(String slug, String title, String emoji) throws Exception {
        return new JSONObject().put("slug", slug).put("title", title).put("emoji", emoji);
    }

    @Test
    public void quickLaunchPutsTheMostPlayedFirst() throws Exception {
        JSONObject shelf = new JSONObject().put("games", new JSONArray()
            .put(game("family-cloud-hop", "Cloud Hop", "☁️"))
            .put(game("family-jubilee", "Jubilee!", "🎺"))
            .put(game("family-checkers", "Checkers", "")));
        JSONObject me = new JSONObject().put("per_game", new JSONArray()
            .put(new JSONObject().put("slug", "family-jubilee").put("my_plays", 9)));
        List<GamesHub.Game> g = GamesHub.quickLaunch(shelf, me, 4);
        assertEquals("family-jubilee", g.get(0).slug);
        assertEquals("family-cloud-hop", g.get(1).slug);
        assertEquals("🎮", g.get(2).emoji);
    }

    @Test
    public void liveRoomsReadAsAJoinableLine() throws Exception {
        JSONObject live = new JSONObject().put("rooms", new JSONArray().put(new JSONObject()
            .put("game", "family-jubilee").put("title", "Jubilee!").put("emoji", "🎺").put("code", "DOVE")
            .put("status", "lobby").put("joinable", true).put("players", new JSONArray().put("Mum").put("Dad"))));
        GamesHub.Room r = GamesHub.live(live, 2).get(0);
        assertEquals("Mum, Dad · waiting for players", r.sub);
        assertTrue(r.joinable);
        assertEquals("/family?tab=games&play=family-jubilee&room=DOVE", r.path());
    }

    @Test
    public void leadersAreTheTopScoresWithMedals() throws Exception {
        JSONObject board = new JSONObject().put("players", new JSONArray()
            .put(new JSONObject().put("display_name", "Robin").put("best_score", 4200))
            .put(new JSONObject().put("display_name", "Jordan").put("best_score", 0))
            .put(new JSONObject().put("display_name", "Me").put("best_score", 1250).put("you", true)));
        List<GamesHub.Leader> l = GamesHub.leaders(board, 3);
        assertEquals(2, l.size());
        assertEquals("🥇 Robin", l.get(0).name);
        assertEquals("1,250", l.get(1).score);
        assertTrue(l.get(1).you);
    }
}
