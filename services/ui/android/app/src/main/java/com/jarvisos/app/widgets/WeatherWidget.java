package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.widget.RemoteViews;
import com.jarvisos.app.JarvisTheme;
import com.jarvisos.app.R;
import java.util.ArrayList;
import java.util.List;
import java.util.TimeZone;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Weather on the home screen, in the user's Jarvis theme: now, today's high
 * and low, and the next five hours, from /api/weather (home, or where you are).
 */
public class WeatherWidget extends AppWidgetProvider {

    private static final int[] H_TIME = {R.id.wx_h1_time, R.id.wx_h2_time, R.id.wx_h3_time, R.id.wx_h4_time, R.id.wx_h5_time};
    private static final int[] H_ICON = {R.id.wx_h1_icon, R.id.wx_h2_icon, R.id.wx_h3_icon, R.id.wx_h4_icon, R.id.wx_h5_icon};
    private static final int[] H_TEMP = {R.id.wx_h1_temp, R.id.wx_h2_temp, R.id.wx_h3_temp, R.id.wx_h4_temp, R.id.wx_h5_temp};

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushWeather(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushWeather(context);
        }
    }

    /** The weather service's icon names, drawn in the theme. */
    static int icon(String name, boolean day) {
        switch (name == null ? "" : name) {
            case "clear": return day ? R.drawable.wx_clear : R.drawable.wx_night;
            case "partly_cloudy": return day ? R.drawable.wx_partly : R.drawable.wx_night;
            case "fog": return R.drawable.wx_fog;
            case "drizzle": case "rain": return R.drawable.wx_rain;
            case "sleet": case "snow": return R.drawable.wx_snow;
            case "storm": return R.drawable.wx_storm;
            default: return R.drawable.wx_cloudy;
        }
    }

    static String deg(JSONObject o, String key) {
        if (o == null || o.isNull(key)) return "-";
        return Math.round(o.optDouble(key)) + "°";
    }

    static RemoteViews build(Context context) {
        RemoteViews base = buildBase(context);
        // The hourly strip needs height; the big icon needs width.
        WidgetFit.Tweak noHours = v -> v.setViewVisibility(R.id.wx_hours, View.GONE);
        WidgetFit.Tweak narrow = v -> {
            v.setViewVisibility(R.id.wx_now_icon, View.GONE);
            v.setTextViewTextSize(R.id.wx_temp, android.util.TypedValue.COMPLEX_UNIT_SP, 32);
            v.setViewLayoutMargin(R.id.wx_temp, RemoteViews.MARGIN_START, 0, android.util.TypedValue.COMPLEX_UNIT_DIP);
        };
        return WidgetFit.of(base)
                .at(100, 100, base, v -> { noHours.apply(v); narrow.apply(v); })
                .at(100, 190, base, narrow)
                .at(240, 100, base, noHours)
                .at(240, 190, base)
                .build();
    }

    static RemoteViews buildBase(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_weather);
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.wx_root, R.id.wx_icon, R.id.wx_title, R.id.wx_refresh, st.accent);
        views.setOnClickPendingIntent(R.id.wx_root, WidgetUpdater.openPathPendingIntent(context, "/", 7220));
        views.setOnClickPendingIntent(R.id.wx_refresh, WidgetUpdater.refreshPendingIntent(context));
        st.icon(views, R.id.wx_now_icon, st.accent);
        st.text(views, R.id.wx_temp, st.text);
        st.text(views, R.id.wx_label, st.text);
        st.text(views, R.id.wx_range, st.muted);
        st.text(views, R.id.wx_feels, st.muted);
        st.tile(views, R.id.wx_hours, false);
        for (int i = 0; i < H_TIME.length; i++) {
            st.text(views, H_TIME[i], st.muted);
            st.text(views, H_TEMP[i], st.text);
            st.icon(views, H_ICON[i], st.second);
        }
        if (WidgetApi.apiKey(context) == null) {
            st.status(views, R.id.wx_status, "Sign in to Jarvis OS");
            return views;
        }
        try {
            JSONObject w = WidgetApi.get(context, "/api/weather");
            JSONObject cur = w.optJSONObject("current");
            JSONObject loc = w.optJSONObject("location");
            String place = loc == null ? "" : EntityNames.text(loc, "name");
            if (!place.isEmpty()) views.setTextViewText(R.id.wx_title, place.toUpperCase(java.util.Locale.ROOT));
            boolean day = cur == null || cur.optBoolean("is_day", true);
            if (cur != null) {
                views.setImageViewResource(R.id.wx_now_icon, icon(EntityNames.text(cur, "icon"), day));
                views.setTextViewText(R.id.wx_temp, deg(cur, "temp"));
                views.setTextViewText(R.id.wx_label, EntityNames.text(cur, "label"));
                JSONObject units = w.optJSONObject("units");
                String wind = cur.isNull("wind") ? "" : " · Wind " + Math.round(cur.optDouble("wind"))
                        + (units == null ? "" : " " + EntityNames.text(units, "wind"));
                views.setTextViewText(R.id.wx_feels, "Feels " + deg(cur, "feels_like") + wind);
            }
            JSONArray daily = w.optJSONArray("daily");
            JSONObject today = daily == null ? null : daily.optJSONObject(0);
            if (today != null) {
                String rain = today.isNull("precip_prob") ? "" : " · " + today.optInt("precip_prob") + "% rain";
                views.setTextViewText(R.id.wx_range, "H " + deg(today, "high") + "  L " + deg(today, "low") + rain);
            }
            JSONArray hourly = w.optJSONArray("hourly");
            long now = System.currentTimeMillis();
            int shown = 0;
            for (int i = 0; hourly != null && i < hourly.length() && shown < H_TIME.length; i++) {
                JSONObject h = hourly.optJSONObject(i);
                if (h == null) continue;
                long t = WidgetTime.parse(EntityNames.text(h, "time"));
                if (t <= 0 || t < now - 30 * 60_000L) continue;
                java.util.Calendar c = java.util.Calendar.getInstance();
                c.setTimeInMillis(t);
                int hr = c.get(java.util.Calendar.HOUR_OF_DAY);
                views.setTextViewText(H_TIME[shown], shown == 0 ? "Now" : (hr % 12 == 0 ? 12 : hr % 12) + (hr < 12 ? "a" : "p"));
                views.setImageViewResource(H_ICON[shown], icon(EntityNames.text(h, "icon"), hr >= 6 && hr < 19));
                views.setTextViewText(H_TEMP[shown], deg(h, "temp"));
                shown++;
            }
            views.setViewVisibility(R.id.wx_hours, shown == 0 ? View.GONE : View.VISIBLE);
        } catch (Exception e) {
            st.status(views, R.id.wx_status, WidgetApi.problem(e).equals("Not set up yet")
                    ? "Set a home location in Jarvis to see the weather" : WidgetApi.problem(e));
        }
        return views;
    }
}
