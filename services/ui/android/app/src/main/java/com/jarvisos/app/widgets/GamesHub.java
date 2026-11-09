package com.jarvisos.app.widgets;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * What the Family Games hub shows, from the family shelf (/api/arcade/family),
 * the scoreboard (/api/arcade/scoreboard) and the live rooms
 * (/api/arcade/live). Pure, so it is tested on the JVM.
 */
public final class GamesHub {

    private GamesHub() {}

    public static final class Game {
        public final String slug;
        public final String title;
        public final String emoji;

        Game(String slug, String title, String emoji) {
            this.slug = slug;
            this.title = title;
            this.emoji = emoji;
        }
    }

    public static final class Room {
        public final String game;
        public final String title;
        public final String emoji;
        public final String code;
        public final String sub;
        public final boolean joinable;

        Room(String game, String title, String emoji, String code, String sub, boolean joinable) {
            this.game = game;
            this.title = title;
            this.emoji = emoji;
            this.code = code;
            this.sub = sub;
            this.joinable = joinable;
        }

        /** The Jarvis path that opens this room (party rooms open the shelf, where the TV link lives). */
        public String path() {
            if (game.startsWith("family-")) return "/family?tab=games&play=" + game + "&room=" + code;
            return "/family?tab=games";
        }
    }

    public static final class Leader {
        public final String name;
        public final String score;
        public final boolean you;

        Leader(String name, String score, boolean you) {
            this.name = name;
            this.score = score;
            this.you = you;
        }
    }

    /** Up to {@code max} games to launch: the ones you play most, then the shelf's order. */
    public static List<Game> quickLaunch(JSONObject shelf, JSONObject me, int max) {
        List<Game> out = new ArrayList<>();
        JSONArray games = shelf == null ? null : shelf.optJSONArray("games");
        if (games == null) return out;
        Map<String, Integer> plays = new HashMap<>();
        JSONArray mine = me == null ? null : me.optJSONArray("per_game");
        for (int i = 0; mine != null && i < mine.length(); i++) {
            JSONObject g = mine.optJSONObject(i);
            if (g != null) plays.put(EntityNames.text(g, "slug"), g.optInt("my_plays", 0));
        }
        List<JSONObject> list = new ArrayList<>();
        for (int i = 0; i < games.length(); i++) {
            JSONObject g = games.optJSONObject(i);
            if (g != null && !EntityNames.text(g, "slug").isEmpty()) list.add(g);
        }
        // Stable sort: most played first, the shelf's own order otherwise.
        Collections.sort(list, (a, b) -> Integer.compare(
                plays.getOrDefault(EntityNames.text(b, "slug"), 0),
                plays.getOrDefault(EntityNames.text(a, "slug"), 0)));
        for (JSONObject g : list) {
            if (out.size() >= max) break;
            String title = EntityNames.text(g, "title");
            String emoji = EntityNames.text(g, "emoji");
            out.add(new Game(EntityNames.text(g, "slug"), title.isEmpty() ? EntityNames.text(g, "slug") : title,
                    emoji.isEmpty() ? "🎮" : emoji));
        }
        return out;
    }

    /** Rooms being played right now: "Mum, Dad · waiting for players". */
    public static List<Room> live(JSONObject live, int max) {
        List<Room> out = new ArrayList<>();
        JSONArray rooms = live == null ? null : live.optJSONArray("rooms");
        for (int i = 0; rooms != null && i < rooms.length() && out.size() < max; i++) {
            JSONObject r = rooms.optJSONObject(i);
            if (r == null || EntityNames.text(r, "code").isEmpty()) continue;
            JSONArray players = r.optJSONArray("players");
            StringBuilder names = new StringBuilder();
            for (int j = 0; players != null && j < players.length() && j < 3; j++) {
                if (names.length() > 0) names.append(", ");
                names.append(players.optString(j));
            }
            if (players != null && players.length() > 3) names.append(" +").append(players.length() - 3);
            boolean joinable = r.optBoolean("joinable", false);
            String state = "lobby".equals(EntityNames.text(r, "status")) ? "waiting for players" : "playing now";
            String sub = names.length() > 0 ? names + " · " + state : state;
            out.add(new Room(EntityNames.text(r, "game"), EntityNames.text(r, "title"), EntityNames.text(r, "emoji"),
                    EntityNames.text(r, "code"), sub, joinable));
        }
        return out;
    }

    /** The family's top {@code max} by best score (members with a score only). */
    public static List<Leader> leaders(JSONObject scoreboard, int max) {
        List<Leader> out = new ArrayList<>();
        JSONArray players = scoreboard == null ? null : scoreboard.optJSONArray("players");
        if (players == null) return out;
        List<JSONObject> list = new ArrayList<>();
        for (int i = 0; i < players.length(); i++) {
            JSONObject p = players.optJSONObject(i);
            if (p != null && p.optInt("best_score", 0) > 0) list.add(p);
        }
        Collections.sort(list, (a, b) -> Integer.compare(b.optInt("best_score", 0), a.optInt("best_score", 0)));
        String[] medals = {"🥇", "🥈", "🥉"};
        for (int i = 0; i < list.size() && i < max; i++) {
            JSONObject p = list.get(i);
            String name = EntityNames.text(p, "display_name");
            if (name.isEmpty()) name = EntityNames.text(p, "username");
            out.add(new Leader((i < medals.length ? medals[i] + " " : "") + name,
                    StatsWidgetFormat.count(p.optInt("best_score", 0)), p.optBoolean("you", false)));
        }
        return out;
    }
}
