package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import java.text.NumberFormat;
import org.json.JSONObject;

/**
 * Home-screen fitness widget.
 *
 * Display only: it shows recorded activity (steps, goal progress, latest
 * workout) and never triggers analysis. Health/fitness analysis runs only when
 * the user asks for it in the app or a schedule they enabled fires.
 */
public class HealthWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushHealth(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushHealth(context);
        }
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_health);
        views.setOnClickPendingIntent(R.id.health_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.health_refresh, WidgetUpdater.refreshPendingIntent(context));

        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(R.id.health_steps, "—");
                views.setTextViewText(R.id.health_goal, "Sign in to Jarvis OS");
                return views;
            }
            JSONObject steps = WidgetApi.activityToday(context);
            int today = steps.optInt("today", 0);
            int goal = steps.optInt("goal", 0);

            NumberFormat fmt = NumberFormat.getIntegerInstance();
            views.setTextViewText(R.id.health_steps, fmt.format(today));

            if (goal > 0) {
                int pct = Math.min(100, (int) Math.round((today * 100.0) / goal));
                views.setProgressBar(R.id.health_progress, 100, pct);
                views.setTextViewText(
                    R.id.health_goal,
                    fmt.format(today) + " of " + fmt.format(goal) + " steps (" + pct + "%)"
                );
            } else {
                views.setProgressBar(R.id.health_progress, 100, 0);
                views.setTextViewText(R.id.health_goal, "No step goal set");
            }
            views.setTextViewText(R.id.health_sub, "");
        } catch (Exception ex) {
            views.setTextViewText(R.id.health_steps, "—");
            views.setTextViewText(R.id.health_goal, "Unavailable");
            views.setTextViewText(
                R.id.health_sub,
                ex.getMessage() != null ? ex.getMessage() : "offline"
            );
        }
        return views;
    }
}
