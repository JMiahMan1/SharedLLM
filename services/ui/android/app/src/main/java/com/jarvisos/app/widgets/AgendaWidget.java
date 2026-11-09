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
 * Agenda on the home screen, in the user's Jarvis theme: what's next on your
 * calendars (today first, then the week), each with its time and a stripe in
 * a theme color per calendar.
 */
public class AgendaWidget extends AppWidgetProvider {

    private static final int[] ROW = {R.id.ag_e1, R.id.ag_e2, R.id.ag_e3, R.id.ag_e4, R.id.ag_e5};
    private static final int[] WHEN = {R.id.ag_e1_when, R.id.ag_e2_when, R.id.ag_e3_when, R.id.ag_e4_when, R.id.ag_e5_when};
    private static final int[] DAY = {R.id.ag_e1_day, R.id.ag_e2_day, R.id.ag_e3_day, R.id.ag_e4_day, R.id.ag_e5_day};
    private static final int[] STRIPE = {R.id.ag_e1_stripe, R.id.ag_e2_stripe, R.id.ag_e3_stripe, R.id.ag_e4_stripe, R.id.ag_e5_stripe};
    private static final int[] TITLE = {R.id.ag_e1_title, R.id.ag_e2_title, R.id.ag_e3_title, R.id.ag_e4_title, R.id.ag_e5_title};
    private static final int[] SUB = {R.id.ag_e1_sub, R.id.ag_e2_sub, R.id.ag_e3_sub, R.id.ag_e4_sub, R.id.ag_e5_sub};

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushAgenda(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushAgenda(context);
        }
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_agenda);
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.ag_root, R.id.ag_icon, R.id.ag_title, R.id.ag_refresh, st.accent);
        views.setOnClickPendingIntent(R.id.ag_root, WidgetUpdater.openPathPendingIntent(context, "/calendar", 7230));
        views.setOnClickPendingIntent(R.id.ag_refresh, WidgetUpdater.refreshPendingIntent(context));
        st.text(views, R.id.ag_date, st.muted);
        st.text(views, R.id.ag_empty, st.muted);
        views.setTextViewText(R.id.ag_date, new java.text.SimpleDateFormat("EEE, MMM d", java.util.Locale.US)
                .format(new java.util.Date()));
        if (WidgetApi.apiKey(context) == null) {
            empty(views, "Sign in to Jarvis OS");
            return views;
        }
        try {
            JSONObject r = WidgetApi.get(context, "/api/communication/calendar/events");
            JSONArray events = r.optJSONArray("events");
            if (events == null && r.optJSONObject("detail") != null) events = r.optJSONObject("detail").optJSONArray("events");
            List<JSONObject> upcoming = new ArrayList<>();
            long now = System.currentTimeMillis();
            for (int i = 0; events != null && i < events.length(); i++) {
                JSONObject e = events.optJSONObject(i);
                if (e == null) continue;
                long end = WidgetTime.parse(EntityNames.text(e, "end_time"));
                long start = WidgetTime.parse(EntityNames.text(e, "start_time"));
                if (start <= 0) continue;
                if ((end > 0 ? end : start + 3_600_000L) < now) continue;
                upcoming.add(e);
            }
            java.util.Collections.sort(upcoming, (a, b) -> Long.compare(
                    WidgetTime.parse(EntityNames.text(a, "start_time")), WidgetTime.parse(EntityNames.text(b, "start_time"))));
            int[] palette = {st.accent, st.second, st.third, st.fourth};
            List<String> calendars = new ArrayList<>();
            TimeZone tz = TimeZone.getDefault();
            for (int i = 0; i < ROW.length; i++) {
                if (i >= upcoming.size()) {
                    views.setViewVisibility(ROW[i], View.GONE);
                    continue;
                }
                JSONObject e = upcoming.get(i);
                long start = WidgetTime.parse(EntityNames.text(e, "start_time"));
                boolean allDay = e.optBoolean("all_day", false);
                boolean started = start < now;
                views.setViewVisibility(ROW[i], View.VISIBLE);
                views.setTextViewText(WHEN[i], allDay ? "All day" : started ? "Now" : WidgetTime.clock(start, tz));
                views.setTextViewText(DAY[i], WidgetTime.day(start, now, tz));
                views.setTextViewText(TITLE[i], EntityNames.text(e, "summary").isEmpty() ? "(No title)" : EntityNames.text(e, "summary"));
                String where = EntityNames.text(e, "location");
                String cal = EntityNames.text(e, "calendar");
                String person = EntityNames.text(e, "person");
                views.setTextViewText(SUB[i], !where.isEmpty() ? where : !person.isEmpty() ? person : cal);
                if (!calendars.contains(cal)) calendars.add(cal);
                int color = palette[calendars.indexOf(cal) % palette.length];
                st.fill(views, STRIPE[i], R.drawable.wg_pill, color);
                st.text(views, WHEN[i], started ? st.accent : st.text);
                st.text(views, DAY[i], st.muted);
                st.text(views, TITLE[i], st.text);
                st.text(views, SUB[i], st.muted);
            }
            if (upcoming.isEmpty()) empty(views, "Nothing coming up this week");
            else views.setViewVisibility(R.id.ag_empty, View.GONE);
        } catch (Exception e) {
            empty(views, WidgetApi.problem(e));
        }
        return views;
    }

    private static void empty(RemoteViews views, String message) {
        for (int id : ROW) views.setViewVisibility(id, View.GONE);
        views.setViewVisibility(R.id.ag_empty, View.VISIBLE);
        views.setTextViewText(R.id.ag_empty, message);
    }
}
