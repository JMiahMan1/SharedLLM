package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

/** Desktop-JVM tests for what the Family Games and Bible widgets print. */
public class StatsWidgetFormatTest {

    private static JSONObject board(JSONObject... players) throws Exception {
        JSONArray list = new JSONArray();
        for (JSONObject p : players) list.put(p);
        return new JSONObject().put("success", true).put("players", list);
    }

    @Test
    public void findsTheSignedInMembersCard() throws Exception {
        JSONObject other = new JSONObject().put("username", "kaleb").put("you", false).put("best_score", 9000);
        JSONObject me = new JSONObject().put("username", "noah").put("you", true).put("submits", 12)
            .put("best_score", 1250).put("crowns", 2)
            .put("per_game", new JSONArray()
                .put(new JSONObject().put("title", "Checkers").put("my_best", 300))
                .put(new JSONObject().put("title", "Jubilee!").put("my_best", 1250)));
        JSONObject mine = StatsWidgetFormat.me(board(other, me));
        assertEquals("12", StatsWidgetFormat.played(mine).value);
        assertEquals("games played", StatsWidgetFormat.played(mine).caption);
        assertEquals("1,250", StatsWidgetFormat.best(mine).value);
        assertEquals("Jubilee!", StatsWidgetFormat.best(mine).caption);
        assertEquals("2", StatsWidgetFormat.crowns(mine).value);
    }

    @Test
    public void aFailedScoreboardShowsDashesNotZeros() throws Exception {
        assertNull(StatsWidgetFormat.me(new JSONObject().put("success", false)));
        assertEquals("-", StatsWidgetFormat.played(null).value);
        assertEquals("-", StatsWidgetFormat.best(null).value);
    }

    @Test
    public void aNewPlayerIsInvitedToPlay() throws Exception {
        JSONObject me = new JSONObject().put("you", true).put("submits", 0).put("best_score", 0);
        assertEquals("Play one!", StatsWidgetFormat.played(me).caption);
        assertEquals("No score yet", StatsWidgetFormat.best(me).caption);
    }

    @Test
    public void bibleTiles() throws Exception {
        JSONObject streaks = new JSONObject().put("read_streak_current", 4).put("read_streak_longest", 11);
        assertEquals("4", StatsWidgetFormat.streak(streaks).value);
        JSONObject broken = new JSONObject().put("read_streak_current", 0).put("read_streak_longest", 11);
        assertEquals("best 11 days", StatsWidgetFormat.streak(broken).caption);

        JSONObject played = new JSONObject().put("entries", new JSONArray()
            .put(new JSONObject().put("you", false).put("correct", 10).put("total", 10))
            .put(new JSONObject().put("you", true).put("correct", 8).put("total", 10)));
        assertEquals("8/10", StatsWidgetFormat.quiz(played).value);
        assertEquals("Play", StatsWidgetFormat.quiz(new JSONObject().put("entries", new JSONArray())).value);

        assertEquals("3", StatsWidgetFormat.due(new JSONObject().put("count", 3)).value);
        assertEquals("all caught up", StatsWidgetFormat.due(new JSONObject().put("count", 0)).caption);
        assertEquals("-", StatsWidgetFormat.due(null).value);
    }
}
