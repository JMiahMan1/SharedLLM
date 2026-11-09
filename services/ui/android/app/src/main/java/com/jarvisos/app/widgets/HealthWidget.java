package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
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

    private static final int[] SPARK_IDS = {
        R.id.health_spark_1, R.id.health_spark_2, R.id.health_spark_3, R.id.health_spark_4,
        R.id.health_spark_5, R.id.health_spark_6, R.id.health_spark_7
    };
    private static final int[] SPARK_DAY_IDS = {
        R.id.health_spark_day_1, R.id.health_spark_day_2, R.id.health_spark_day_3, R.id.health_spark_day_4,
        R.id.health_spark_day_5, R.id.health_spark_day_6, R.id.health_spark_day_7
    };
    private static final int SPARK_MAX_DP = 34;

    static RemoteViews build(Context context) {
        RemoteViews base = buildBase(context);
        WidgetFit.Tweak ringOnly = v -> v.setViewVisibility(R.id.health_side, View.GONE);
        WidgetFit.Tweak smallRing = v -> {
            WidgetFit.sizeDp(v, R.id.health_ring, 66, 66);
            v.setTextViewTextSize(R.id.health_steps, android.util.TypedValue.COMPLEX_UNIT_SP, 14);
            v.setViewVisibility(R.id.health_ring_caption, View.GONE);
        };
        return WidgetFit.of(base).fallback(ringOnly)
                .at(100, 100, base, v -> { ringOnly.apply(v); smallRing.apply(v); })
                .at(100, 150, base, ringOnly)
                .at(240, 100, base, smallRing)
                .at(240, 150, base)
                .build();
    }

    static RemoteViews buildBase(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_health);
        views.setOnClickPendingIntent(R.id.health_root, WidgetUpdater.openPathPendingIntent(context, "/fitness", 6810));
        views.setOnClickPendingIntent(R.id.health_refresh, WidgetUpdater.refreshPendingIntent(context));
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.health_root, R.id.health_icon, R.id.health_title, R.id.health_refresh, st.accent);
        st.text(views, R.id.health_steps, st.text);
        st.text(views, R.id.health_ring_caption, st.muted);
        st.text(views, R.id.health_goal, st.text);
        st.text(views, R.id.health_sub, st.muted);
        st.progress(views, R.id.health_ring, 100, 0, st.progress);
        for (int i = 0; i < SPARK_IDS.length; i++) {
            st.fill(views, SPARK_IDS[i], R.drawable.wg_pill, st.track);
            st.text(views, SPARK_DAY_IDS[i], st.faint);
        }

        if (WidgetApi.apiKey(context) == null) {
            st.status(views, R.id.health_status, "Sign in to Jarvis OS");
            return views;
        }
        try {
            JSONObject steps = WidgetApi.get(context, "/api/geo/steps?days=7");
            JSONObject daily = steps.optJSONObject("daily_steps");
            int today = steps.optInt("today", 0);
            int goal = steps.optInt("goal", 0);
            NumberFormat fmt = NumberFormat.getIntegerInstance(java.util.Locale.US);
            if (daily == null || daily.length() == 0) {
                views.setTextViewText(R.id.health_steps, "-");
                views.setTextViewText(R.id.health_goal, "No steps yet");
                views.setTextViewText(R.id.health_sub, "Open Jarvis to start tracking");
                return views;
            }
            java.util.List<String> days = new java.util.ArrayList<>();
            JSONArray names = daily.names();
            for (int i = 0; names != null && i < names.length(); i++) days.add(names.getString(i));
            java.util.Collections.sort(days);
            if (days.size() > 7) days = days.subList(days.size() - 7, days.size());
            int total = 0;
            int max = Math.max(goal, 1);
            for (String d : days) {
                int n = daily.optInt(d, 0);
                total += n;
                max = Math.max(max, n);
            }
            int avg = total / days.size();
            views.setTextViewText(R.id.health_steps, fmt.format(today));
            if (goal > 0) {
                int pct = HealthTodayFormat.percent(today, goal);
                boolean done = today >= goal;
                views.setTextViewText(R.id.health_goal, done ? "Goal reached!" : pct + "% of " + fmt.format(goal));
                st.text(views, R.id.health_goal, done ? st.third : st.text);
                st.progress(views, R.id.health_ring, 100, pct, done ? st.third : st.progress);
            } else {
                views.setTextViewText(R.id.health_goal, "steps today");
            }
            views.setTextViewText(R.id.health_sub, "Avg " + fmt.format(avg) + " a day · " + days.size()
                    + (days.size() == 1 ? " day" : " days"));
            // The chart: one bar per day, today's in the accent (heights need Android 12+).
            int offset = SPARK_IDS.length - days.size();
            for (int i = 0; i < SPARK_IDS.length; i++) {
                int di = i - offset;
                if (di < 0) {
                    views.setViewVisibility(SPARK_IDS[i], View.INVISIBLE);
                    continue;
                }
                String day = days.get(di);
                int n = daily.optInt(day, 0);
                boolean isToday = di == days.size() - 1;
                boolean hitGoal = goal > 0 && n >= goal;
                st.fill(views, SPARK_IDS[i], R.drawable.wg_pill,
                        isToday ? st.progress : hitGoal ? com.jarvisos.app.JarvisTheme.withAlpha(st.third, 0.8f) : com.jarvisos.app.JarvisTheme.withAlpha(st.text, 0.28f));
                views.setTextViewText(SPARK_DAY_IDS[i], dayInitial(day));
                if (isToday) st.text(views, SPARK_DAY_IDS[i], st.text);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    float h = Math.max(4f, SPARK_MAX_DP * n / (float) max);
                    views.setViewLayoutHeight(SPARK_IDS[i], h, android.util.TypedValue.COMPLEX_UNIT_DIP);
                }
            }
        } catch (Exception ex) {
            views.setTextViewText(R.id.health_steps, "-");
            st.status(views, R.id.health_status, ex.getMessage() != null ? ex.getMessage() : "Offline");
        }
        return views;
    }

    /** "2026-11-08" → "S" (the weekday's initial). */
    static String dayInitial(String isoDay) {
        try {
            java.text.SimpleDateFormat f = new java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US);
            java.util.Date d = f.parse(isoDay);
            if (d == null) return "";
            return new java.text.SimpleDateFormat("EEEEE", java.util.Locale.US).format(d);
        } catch (Exception e) {
            return "";
        }
    }
}
