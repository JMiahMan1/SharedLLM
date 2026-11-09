package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
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
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.ht_root, R.id.ht_icon, R.id.ht_title, R.id.ht_refresh, st.fourth);
        st.stat(views, R.id.ht_steps_tile, R.id.ht_steps_icon, R.id.ht_steps_label, R.id.ht_steps_value,
                R.id.ht_steps_caption, st.accent, false);
        st.stat(views, R.id.ht_ready_tile, R.id.ht_ready_icon, R.id.ht_ready_label, R.id.ht_ready_value,
                R.id.ht_ready_caption, st.third, false);
        st.stat(views, R.id.ht_sleep_tile, R.id.ht_sleep_icon, R.id.ht_sleep_label, R.id.ht_sleep_value,
                R.id.ht_sleep_caption, st.second, false);
        st.stat(views, R.id.ht_water_tile, R.id.ht_water_icon, R.id.ht_water_label, R.id.ht_water_value,
                R.id.ht_water_caption, st.fourth, false);
        st.progress(views, R.id.ht_steps_bar, 100, 0, st.progress);
        st.progress(views, R.id.ht_water_bar, 100, 0, st.fourth);

        if (WidgetApi.apiKey(context) == null) {
            st.status(views, R.id.ht_status, "Sign in to Jarvis OS");
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

        if (steps != null) {
            int goal = steps.optInt("goal", 0);
            st.progress(views, R.id.ht_steps_bar, 100, HealthTodayFormat.percent(steps.optInt("today", 0), goal), st.progress);
        }
        JSONObject water = summary == null ? null : summary.optJSONObject("water");
        if (water != null && !water.isNull("goal_ml")) {
            st.progress(views, R.id.ht_water_bar, 100,
                    HealthTodayFormat.percent((int) water.optDouble("ml", 0), (int) water.optDouble("goal_ml", 0)), st.fourth);
        }
        JSONObject ready = summary == null ? null : summary.optJSONObject("readiness");
        if (ready != null && !ready.isNull("score")) {
            int score = ready.optInt("score");
            if (score >= 70) st.tileTint(views, R.id.ht_ready_tile, st.third);
        }
        st.status(views, R.id.ht_status, summary == null && steps == null ? (problem != null ? problem : "Offline") : null);
        return views;
    }
}
