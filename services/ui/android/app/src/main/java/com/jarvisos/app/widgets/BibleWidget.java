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
 * Bible on the home screen: the signed-in reader's own reading streak, today's
 * daily quiz and the memory verses due, read with their own key. Each tile
 * opens its page (reading, the quiz, memory verses); the card opens the Bible.
 */
public class BibleWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushBible(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushBible(context);
        }
    }

    private static void tile(RemoteViews views, int valueId, int captionId, StatsWidgetFormat.Tile tile) {
        views.setTextViewText(valueId, tile.value);
        views.setTextViewText(captionId, tile.caption);
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_bible);
        views.setOnClickPendingIntent(R.id.bw_root, WidgetUpdater.openPathPendingIntent(context, "/bible", 7110));
        views.setOnClickPendingIntent(R.id.bw_streak_tile, WidgetUpdater.openPathPendingIntent(context, "/bible", 7111));
        views.setOnClickPendingIntent(R.id.bw_quiz_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=play", 7112));
        views.setOnClickPendingIntent(R.id.bw_due_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=memorize", 7113));
        views.setOnClickPendingIntent(R.id.bw_refresh, WidgetUpdater.refreshPendingIntent(context));

        if (WidgetApi.apiKey(context) == null) {
            views.setViewVisibility(R.id.bw_status, View.VISIBLE);
            views.setTextViewText(R.id.bw_status, "Sign in to Jarvis OS");
            return views;
        }

        // Three independent reads: one failing leaves the others' tiles honest.
        JSONObject streaks = null;
        JSONObject quiz = null;
        JSONObject due = null;
        String problem = null;
        try {
            streaks = WidgetApi.get(context, "/api/bible/streaks");
        } catch (Exception e) {
            problem = e.getMessage();
        }
        try {
            quiz = WidgetApi.get(context, "/api/bible/games/leaderboard");
        } catch (Exception e) {
            if (problem == null) problem = e.getMessage();
        }
        try {
            due = WidgetApi.get(context, "/api/bible/memorize/due?limit=100");
        } catch (Exception e) {
            if (problem == null) problem = e.getMessage();
        }

        tile(views, R.id.bw_streak_value, R.id.bw_streak_caption, StatsWidgetFormat.streak(streaks));
        tile(views, R.id.bw_quiz_value, R.id.bw_quiz_caption, StatsWidgetFormat.quiz(quiz));
        tile(views, R.id.bw_due_value, R.id.bw_due_caption, StatsWidgetFormat.due(due));

        boolean nothing = streaks == null && quiz == null && due == null;
        views.setViewVisibility(R.id.bw_status, nothing ? View.VISIBLE : View.GONE);
        views.setTextViewText(R.id.bw_status, nothing ? (problem != null ? problem : "Offline") : "");
        return views;
    }
}
