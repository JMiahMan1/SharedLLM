package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import org.json.JSONObject;

/**
 * Family Games on the home screen: the signed-in member's own arcade card
 * (games played, best score, #1 spots) from the family scoreboard, read with
 * their own key like the other widgets. A tap opens Family → Games.
 */
public class GamesWidget extends AppWidgetProvider {

    static final String PATH = "/family?tab=games";

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

    private static void tile(RemoteViews views, int valueId, int captionId, StatsWidgetFormat.Tile tile) {
        views.setTextViewText(valueId, tile.value);
        views.setTextViewText(captionId, tile.caption);
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_games);
        views.setOnClickPendingIntent(R.id.gw_root, WidgetUpdater.openPathPendingIntent(context, PATH, 7100));
        views.setOnClickPendingIntent(R.id.gw_played_tile, WidgetUpdater.openPathPendingIntent(context, PATH, 7101));
        views.setOnClickPendingIntent(R.id.gw_best_tile, WidgetUpdater.openPathPendingIntent(context, PATH, 7102));
        views.setOnClickPendingIntent(R.id.gw_crowns_tile, WidgetUpdater.openPathPendingIntent(context, PATH, 7103));
        views.setOnClickPendingIntent(R.id.gw_refresh, WidgetUpdater.refreshPendingIntent(context));

        if (WidgetApi.apiKey(context) == null) {
            views.setViewVisibility(R.id.gw_status, View.VISIBLE);
            views.setTextViewText(R.id.gw_status, "Sign in to Jarvis OS");
            return views;
        }

        JSONObject me = null;
        String problem = null;
        try {
            JSONObject board = WidgetApi.get(context, "/api/arcade/scoreboard");
            me = StatsWidgetFormat.me(board);
            if (me == null) {
                problem = board != null && !board.optBoolean("arcade_available", true)
                        ? "The arcade is offline" : "No player card yet";
            } else {
                String name = me.optString("display_name", "");
                if (!name.isEmpty()) views.setTextViewText(R.id.gw_title, "FAMILY GAMES · " + name.toUpperCase());
            }
        } catch (Exception e) {
            problem = e.getMessage() != null ? e.getMessage() : "Offline";
        }

        tile(views, R.id.gw_played_value, R.id.gw_played_caption, StatsWidgetFormat.played(me));
        tile(views, R.id.gw_best_value, R.id.gw_best_caption, StatsWidgetFormat.best(me));
        tile(views, R.id.gw_crowns_value, R.id.gw_crowns_caption, StatsWidgetFormat.crowns(me));

        views.setViewVisibility(R.id.gw_status, problem == null ? View.GONE : View.VISIBLE);
        views.setTextViewText(R.id.gw_status, problem == null ? "" : problem);
        return views;
    }
}
