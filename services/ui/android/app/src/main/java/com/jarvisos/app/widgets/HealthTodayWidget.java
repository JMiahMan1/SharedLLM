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
 * Health today on the home screen (Health hub Phase 8): steps, readiness,
 * last night's sleep and water, read with the signed-in person's own key the
 * same way the other widgets are (TokenBridge → {@link WidgetApi}). Display
 * only; a tap opens the Health page (route `/fitness`). The step-only "Fitness & Health" widget
 * is unchanged beside it.
 */
public class HealthTodayWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushHealthToday(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushHealthToday(context);
        }
    }

    private static void tile(RemoteViews views, int valueId, int captionId, HealthTodayFormat.Tile tile) {
        views.setTextViewText(valueId, tile.value);
        views.setTextViewText(captionId, tile.caption);
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_health_today);
        views.setOnClickPendingIntent(R.id.ht_root, WidgetUpdater.openPathPendingIntent(context, "/fitness", 6800));
        views.setOnClickPendingIntent(R.id.ht_refresh, WidgetUpdater.refreshPendingIntent(context));

        if (WidgetApi.apiKey(context) == null) {
            views.setViewVisibility(R.id.ht_status, View.VISIBLE);
            views.setTextViewText(R.id.ht_status, "Sign in to Jarvis OS");
            return views;
        }

        // Two independent reads: one failing leaves the other's tiles honest
        // ("Unavailable") instead of blanking the whole widget.
        JSONObject summary = null;
        JSONObject steps = null;
        String problem = null;
        try {
            summary = WidgetApi.get(context, "/api/health/summary");
        } catch (Exception e) {
            problem = e.getMessage();
        }
        try {
            steps = WidgetApi.activityToday(context);
        } catch (Exception e) {
            if (problem == null) problem = e.getMessage();
        }

        tile(views, R.id.ht_steps_value, R.id.ht_steps_caption, HealthTodayFormat.steps(steps));
        tile(views, R.id.ht_ready_value, R.id.ht_ready_caption, HealthTodayFormat.readiness(summary));
        tile(views, R.id.ht_sleep_value, R.id.ht_sleep_caption, HealthTodayFormat.sleep(summary));
        tile(views, R.id.ht_water_value, R.id.ht_water_caption, HealthTodayFormat.water(summary));

        if (summary == null && steps == null) {
            views.setViewVisibility(R.id.ht_status, View.VISIBLE);
            views.setTextViewText(R.id.ht_status, problem != null ? problem : "Offline");
        } else {
            views.setViewVisibility(R.id.ht_status, View.GONE);
        }
        return views;
    }
}
