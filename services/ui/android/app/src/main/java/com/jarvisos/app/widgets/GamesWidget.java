package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.view.View;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import java.util.List;
import org.json.JSONObject;

/**
 * Family Games on the home screen, in the user's Jarvis theme.
 *
 * <p>Full size (the hub): games being played right now with a Join button,
 * quick-launch tiles for your games (straight into the game), the family's
 * high scores and your own card. Small size: your played / best / #1 spots.
 */
public class GamesWidget extends AppWidgetProvider {

    static final String PATH = "/family?tab=games";

    private static final int[] LIVE = {R.id.gh_live_1, R.id.gh_live_2};
    private static final int[] LIVE_EMOJI = {R.id.gh_live_1_emoji, R.id.gh_live_2_emoji};
    private static final int[] LIVE_TITLE = {R.id.gh_live_1_title, R.id.gh_live_2_title};
    private static final int[] LIVE_SUB = {R.id.gh_live_1_sub, R.id.gh_live_2_sub};
    private static final int[] LIVE_JOIN = {R.id.gh_live_1_join, R.id.gh_live_2_join};
    private static final int[] GAME = {R.id.gh_game_1, R.id.gh_game_2, R.id.gh_game_3, R.id.gh_game_4};
    private static final int[] GAME_EMOJI = {R.id.gh_game_1_emoji, R.id.gh_game_2_emoji, R.id.gh_game_3_emoji, R.id.gh_game_4_emoji};
    private static final int[] GAME_NAME = {R.id.gh_game_1_name, R.id.gh_game_2_name, R.id.gh_game_3_name, R.id.gh_game_4_name};
    private static final int[] LEAD = {R.id.gh_lead_1, R.id.gh_lead_2, R.id.gh_lead_3};
    private static final int[] LEAD_NAME = {R.id.gh_lead_1_name, R.id.gh_lead_2_name, R.id.gh_lead_3_name};
    private static final int[] LEAD_SCORE = {R.id.gh_lead_1_score, R.id.gh_lead_2_score, R.id.gh_lead_3_score};

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushGames(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushGames(context);
        }
    }

    /** Everything the widget reads, fetched once for both sizes. */
    static final class Data {
        boolean signedIn;
        JSONObject board;
        JSONObject shelf;
        JSONObject live;
        String problem;

        JSONObject me() {
            return StatsWidgetFormat.me(board);
        }
    }

    static Data fetch(Context context) {
        Data d = new Data();
        d.signedIn = WidgetApi.apiKey(context) != null;
        if (!d.signedIn) return d;
        try { d.board = WidgetApi.get(context, "/api/arcade/scoreboard"); } catch (Exception e) { d.problem = e.getMessage(); }
        try { d.shelf = WidgetApi.get(context, "/api/arcade/family"); } catch (Exception e) { if (d.problem == null) d.problem = e.getMessage(); }
        try { d.live = WidgetApi.get(context, "/api/arcade/live"); } catch (Exception ignored) { }
        return d;
    }

    private static final int[] S_TILE = {R.id.gw_played_tile, R.id.gw_best_tile, R.id.gw_crowns_tile};
    private static final int[] S_LABEL = {R.id.gw_played_label, R.id.gw_best_label, R.id.gw_crowns_label};
    private static final int[] S_VALUE = {R.id.gw_played_value, R.id.gw_best_value, R.id.gw_crowns_value};
    private static final int[] S_CAPTION = {R.id.gw_played_caption, R.id.gw_best_caption, R.id.gw_crowns_caption};

    static RemoteViews build(Context context) {
        Data d = fetch(context);
        RemoteViews compact = buildCompact(context, d);
        // Narrow tiles wrap captions; the best game's title may be one long word.
        StatsWidgetFormat.Tile best = StatsWidgetFormat.best(d.me());
        String bestNarrow = StatsWidgetFormat.narrowCaption(best.caption, "best score");
        WidgetFit.Tweak narrowBest = v -> {
            v.setTextViewText(R.id.gw_best_caption, bestNarrow);
            v.setContentDescription(R.id.gw_best_tile, "Best score " + best.value
                    + (best.caption.isEmpty() ? "" : ", " + best.caption));
        };
        RemoteViews full = buildFull(context, d, 1);
        WidgetFit fit = WidgetFit.of(full)
                .at(100, 80, compact, WidgetFit.stats(S_TILE, S_LABEL, S_VALUE, S_CAPTION, true, true))
                .at(100, 150, compact, v -> {
                    WidgetFit.stats(S_TILE, S_LABEL, S_VALUE, S_CAPTION, true, false).apply(v);
                    narrowBest.apply(v);
                })
                .at(250, 80, compact, WidgetFit.stats(S_TILE, S_LABEL, S_VALUE, S_CAPTION, false, true))
                .at(250, 150, compact)
                .at(240, 230, full);
        // Room for both live games only on a taller widget.
        fit.at(240, 320, buildFull(context, d, 2));
        return fit.build();
    }

    static RemoteViews buildCompact(Context context) {
        return buildCompact(context, fetch(context));
    }

    static RemoteViews buildFull(Context context) {
        return buildFull(context, fetch(context), LIVE.length);
    }

    private static void tile(RemoteViews views, int valueId, int captionId, StatsWidgetFormat.Tile tile) {
        views.setTextViewText(valueId, tile.value);
        views.setTextViewText(captionId, tile.caption);
    }

    static RemoteViews buildCompact(Context context, Data d) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_games);
        views.setOnClickPendingIntent(R.id.gw_root, WidgetUpdater.openPathPendingIntent(context, PATH, 7100));
        views.setOnClickPendingIntent(R.id.gw_played_tile, WidgetUpdater.openPathPendingIntent(context, PATH, 7101));
        views.setOnClickPendingIntent(R.id.gw_best_tile, WidgetUpdater.openPathPendingIntent(context, PATH, 7102));
        views.setOnClickPendingIntent(R.id.gw_crowns_tile, WidgetUpdater.openPathPendingIntent(context, PATH, 7103));
        views.setOnClickPendingIntent(R.id.gw_refresh, WidgetUpdater.refreshPendingIntent(context));
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.gw_root, R.id.gw_icon, R.id.gw_title, R.id.gw_refresh, st.accent);
        st.stat(views, R.id.gw_played_tile, R.id.gw_played_icon, R.id.gw_played_label,
                R.id.gw_played_value, R.id.gw_played_caption, st.accent, false);
        st.stat(views, R.id.gw_best_tile, R.id.gw_best_icon, R.id.gw_best_label,
                R.id.gw_best_value, R.id.gw_best_caption, st.second, false);
        st.stat(views, R.id.gw_crowns_tile, R.id.gw_crowns_icon, R.id.gw_crowns_label,
                R.id.gw_crowns_value, R.id.gw_crowns_caption, st.fourth, false);
        if (!d.signedIn) {
            st.status(views, R.id.gw_status, "Sign in to Jarvis OS");
            return views;
        }
        JSONObject me = d.me();
        String problem = d.problem;
        if (me == null && problem == null) {
            problem = d.board != null && !d.board.optBoolean("arcade_available", true)
                    ? "The arcade is offline" : "No player card yet";
        }
        if (me != null) {
            String name = me.optString("display_name", "");
            if (!name.isEmpty()) views.setTextViewText(R.id.gw_title, "FAMILY GAMES · " + name.toUpperCase());
        }
        tile(views, R.id.gw_played_value, R.id.gw_played_caption, StatsWidgetFormat.played(me));
        tile(views, R.id.gw_best_value, R.id.gw_best_caption, StatsWidgetFormat.best(me));
        tile(views, R.id.gw_crowns_value, R.id.gw_crowns_caption, StatsWidgetFormat.crowns(me));
        if (me != null && me.optInt("crowns", 0) > 0) st.tileTint(views, R.id.gw_crowns_tile, st.fourth);
        st.status(views, R.id.gw_status, me == null ? problem : null);
        return views;
    }

    static RemoteViews buildFull(Context context, Data d, int maxLive) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_games_hub);
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.gh_root, R.id.gh_icon, R.id.gh_title, R.id.gh_refresh, st.accent);
        views.setOnClickPendingIntent(R.id.gh_root, WidgetUpdater.openPathPendingIntent(context, PATH, 7104));
        views.setOnClickPendingIntent(R.id.gh_refresh, WidgetUpdater.refreshPendingIntent(context));
        st.text(views, R.id.gh_me, st.muted);
        st.text(views, R.id.gh_live_label, st.accent);
        st.text(views, R.id.gh_play_label, st.muted);
        st.text(views, R.id.gh_lead_label, st.muted);
        if (!d.signedIn) {
            st.status(views, R.id.gh_status, "Sign in to Jarvis OS");
            return views;
        }

        JSONObject me = d.me();
        if (me != null) {
            int played = me.optInt("submits", 0);
            int best = me.optInt("best_score", 0);
            views.setTextViewText(R.id.gh_me, played + " played" + (best > 0 ? " · best " + StatsWidgetFormat.count(best) : ""));
        }

        // Playing now: up to two rooms, each a tap straight into it.
        List<GamesHub.Room> rooms = GamesHub.live(d.live, Math.min(maxLive, LIVE.length));
        views.setViewVisibility(R.id.gh_live_label, rooms.isEmpty() ? View.GONE : View.VISIBLE);
        for (int i = 0; i < LIVE.length; i++) {
            if (i >= rooms.size()) {
                views.setViewVisibility(LIVE[i], View.GONE);
                continue;
            }
            GamesHub.Room r = rooms.get(i);
            views.setViewVisibility(LIVE[i], View.VISIBLE);
            st.tile(views, LIVE[i], true);
            views.setTextViewText(LIVE_EMOJI[i], r.emoji);
            views.setTextViewText(LIVE_TITLE[i], r.title + " · " + r.code);
            views.setTextViewText(LIVE_SUB[i], r.sub);
            views.setTextViewText(LIVE_JOIN[i], r.joinable ? "Join" : "Open");
            st.text(views, LIVE_TITLE[i], st.text);
            st.text(views, LIVE_SUB[i], st.muted);
            st.fill(views, LIVE_JOIN[i], R.drawable.wg_pill, st.accent);
            st.text(views, LIVE_JOIN[i], st.onAccent);
            views.setOnClickPendingIntent(LIVE[i], WidgetUpdater.openPathPendingIntent(context, r.path(), 7150 + i));
        }

        // Play: your games, one tap into each.
        List<GamesHub.Game> games = GamesHub.quickLaunch(d.shelf, me, GAME.length);
        for (int i = 0; i < GAME.length; i++) {
            if (i >= games.size()) {
                views.setViewVisibility(GAME[i], View.INVISIBLE);
                continue;
            }
            GamesHub.Game g = games.get(i);
            views.setViewVisibility(GAME[i], View.VISIBLE);
            st.tile(views, GAME[i], false);
            views.setTextViewText(GAME_EMOJI[i], g.emoji);
            views.setTextViewText(GAME_NAME[i], g.title);
            st.text(views, GAME_NAME[i], st.text);
            views.setContentDescription(GAME[i], "Play " + g.title);
            views.setOnClickPendingIntent(GAME[i], WidgetUpdater.openPathPendingIntent(context,
                    PATH + "&play=" + android.net.Uri.encode(g.slug), 7140 + i));
        }

        // High scores: the family's top three, you highlighted.
        List<GamesHub.Leader> leaders = GamesHub.leaders(d.board, LEAD.length);
        views.setViewVisibility(R.id.gh_lead_label, leaders.isEmpty() ? View.GONE : View.VISIBLE);
        for (int i = 0; i < LEAD.length; i++) {
            if (i >= leaders.size()) {
                views.setViewVisibility(LEAD[i], leaders.isEmpty() ? View.GONE : View.INVISIBLE);
                continue;
            }
            GamesHub.Leader l = leaders.get(i);
            views.setViewVisibility(LEAD[i], View.VISIBLE);
            st.tile(views, LEAD[i], l.you);
            views.setTextViewText(LEAD_NAME[i], l.name);
            views.setTextViewText(LEAD_SCORE[i], l.score);
            st.text(views, LEAD_NAME[i], st.text);
            st.text(views, LEAD_SCORE[i], l.you ? st.accent : st.muted);
            views.setOnClickPendingIntent(LEAD[i], WidgetUpdater.openPathPendingIntent(context, PATH, 7160 + i));
        }

        String problem = null;
        if (games.isEmpty()) {
            problem = d.shelf != null && !d.shelf.optBoolean("arcade_available", true) ? "The arcade is offline"
                    : d.problem != null ? d.problem : "No games on the family shelf yet";
        }
        st.status(views, R.id.gh_status, problem);
        return views;
    }
}
