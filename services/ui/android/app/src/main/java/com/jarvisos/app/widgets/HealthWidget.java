package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.os.Build;
import android.view.View;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import java.text.NumberFormat;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Home-screen fitness widget, kept in step with the in-app Health widget:
 * same data source, and the same theme colors.
 *
 * Display only: it never triggers analysis. Health/fitness analysis runs only
 * when the user asks for it in the app or a schedule they enabled fires.
 *
 * The colors come from the widget's own config (published by the app from the
 * active theme pack), so palettes stay in the pack data and are never
 * hardcoded here.
 */
public class HealthWidget extends AppWidgetProvider {

    private static final String DEFAULT_ACCENT = "#863BFF";
    private static final String DEFAULT_TEXT = "#F1F5F9";

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

    /** Theme colors + goal published by the app when the user picks a theme. */
    private static JSONObject healthConfig(Context context) {
        JSONObject empty = new JSONObject();
        try {
            JSONObject resp = WidgetApi.get(context, "/api/widgets/settings");
            JSONArray widgets = resp.optJSONArray("widgets");
            if (widgets == null) return empty;
            for (int i = 0; i < widgets.length(); i++) {
                JSONObject w = widgets.getJSONObject(i);
                if (!"health_activity".equals(w.optString("widget_key"))) continue;
                JSONObject cfg = w.optJSONObject("config");
                return cfg != null ? cfg : empty;
            }
        } catch (Exception e) {
            // fall through to defaults
        }
        return empty;
    }

    private static int color(String hex, int fallback) {
        if (hex == null || hex.isEmpty()) return fallback;
        try {
            return Color.parseColor(hex);
        } catch (Exception e) {
            return fallback;
        }
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_health);
        views.setOnClickPendingIntent(R.id.health_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.health_refresh, WidgetUpdater.refreshPendingIntent(context));

        int accent = color(DEFAULT_ACCENT, Color.parseColor(DEFAULT_ACCENT));
        int textColor = Color.parseColor(DEFAULT_TEXT);
        // Until a ring has something to say it is hidden rather than drawn
        // empty, which would read as "you are at 0% of your goal".
        views.setViewVisibility(R.id.health_ring, View.GONE);

        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(R.id.health_steps, "—");
                views.setTextViewText(R.id.health_goal, "Sign in to Jarvis OS");
                return views;
            }

            JSONObject cfg = healthConfig(context);
            accent = color(cfg.optString("themeAccent"), accent);
            textColor = color(cfg.optString("themeAccentText"), textColor);
            views.setTextColor(R.id.health_steps, textColor);
            // Reflective call: setProgressTintList only exists on API 31+, and
            // RemoteViews ignores unknown methods instead of crashing.
            views.setInt(R.id.health_progress, "setProgressTintList", accent);

            // 7-day window so the widget can show a real average alongside today.
            JSONObject steps = WidgetApi.get(context, "/api/geo/steps?days=7");
            JSONObject daily = steps.optJSONObject("daily_steps");
            int days = daily == null ? 0 : daily.length();
            if (days == 0) {
                // Through HealthRingCalc, so "never recorded" and "no goal" stay
                // the distinct states they are rather than collapsing into one
                // caption on one path and not the other.
                HealthRingCalc.Ring ring =
                    HealthRingCalc.build(0, steps.optInt("goal", 0), "—", false);
                views.setTextViewText(R.id.health_steps, "—");
                views.setTextViewText(R.id.health_goal, ring.caption);
                views.setTextViewText(R.id.health_sub, "Open the app to start tracking");
                views.setProgressBar(R.id.health_progress, 100, 0, false);
                return views;
            }

            int today = steps.optInt("today", 0);
            int goal = steps.optInt("goal", 0);
            int total = 0;
            for (int i = 0; i < daily.length(); i++) {
                total += daily.optInt(String.valueOf(daily.names().get(i)), 0);
            }
            int avg = total / days;

            NumberFormat fmt = NumberFormat.getIntegerInstance();
            views.setTextViewText(R.id.health_steps, fmt.format(today));
            views.setTextViewText(
                R.id.health_sub,
                fmt.format(avg) + " avg over " + days + (days == 1 ? " day" : " days")
            );

            // The ring and the bar come from one calculation, so they can never
            // disagree about what today's number means.
            HealthRingCalc.Ring ring = HealthRingCalc.build(today, goal, fmt.format(today), true);
            views.setTextViewText(R.id.health_goal, ring.caption);
            if (ring.percent < 0) {
                views.setViewVisibility(R.id.health_ring, View.GONE);
                views.setProgressBar(R.id.health_progress, 100, 0, false);
            } else {
                views.setViewVisibility(R.id.health_ring, View.VISIBLE);
                views.setProgressBar(R.id.health_ring, 100, ring.percent, false);
                // setColorStateList, not setInt: the method takes a
                // ColorStateList and an int argument would be ignored.
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    views.setColorStateList(
                        R.id.health_ring,
                        "setProgressTintList",
                        ColorStateList.valueOf(accent)
                    );
                }
                views.setProgressBar(R.id.health_progress, 100, ring.percent, false);
            }
        } catch (Exception ex) {
            views.setTextViewText(R.id.health_steps, "—");
            views.setTextViewText(R.id.health_goal, "Unavailable");
            views.setTextViewText(
                R.id.health_sub,
                ex.getMessage() != null ? ex.getMessage() : "offline"
            );
            views.setViewVisibility(R.id.health_ring, View.GONE);
        }
        return views;
    }
}
