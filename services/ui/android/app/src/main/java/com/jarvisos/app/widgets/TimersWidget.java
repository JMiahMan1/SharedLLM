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
 * Timers and alarms on the home screen, in the user's Jarvis theme: running
 * timers count down live (the platform Chronometer, no refresh needed), and
 * alarms show when they ring.
 */
public class TimersWidget extends AppWidgetProvider {

    private static final int[] ROW = {R.id.tm_t1, R.id.tm_t2, R.id.tm_t3};
    private static final int[] ICON = {R.id.tm_t1_icon, R.id.tm_t2_icon, R.id.tm_t3_icon};
    private static final int[] TITLE = {R.id.tm_t1_title, R.id.tm_t2_title, R.id.tm_t3_title};
    private static final int[] SUB = {R.id.tm_t1_sub, R.id.tm_t2_sub, R.id.tm_t3_sub};
    private static final int[] CLOCK = {R.id.tm_t1_clock, R.id.tm_t2_clock, R.id.tm_t3_clock};
    private static final int[] AT = {R.id.tm_t1_at, R.id.tm_t2_at, R.id.tm_t3_at};

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushTimers(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushTimers(context);
        }
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_timers);
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.tm_root, R.id.tm_icon, R.id.tm_title, R.id.tm_refresh, st.accent);
        views.setOnClickPendingIntent(R.id.tm_root, WidgetUpdater.openPathPendingIntent(context, "/family?tab=announce", 7260));
        views.setOnClickPendingIntent(R.id.tm_refresh, WidgetUpdater.refreshPendingIntent(context));
        st.text(views, R.id.tm_empty, st.muted);
        if (WidgetApi.apiKey(context) == null) {
            empty(views, "Sign in to Jarvis OS");
            return views;
        }
        try {
            JSONObject r = WidgetApi.get(context, "/api/communication/timers");
            JSONArray items = r.optJSONArray("items");
            long now = System.currentTimeMillis();
            List<JSONObject> list = new ArrayList<>();
            for (int i = 0; items != null && i < items.length(); i++) {
                JSONObject t = items.optJSONObject(i);
                if (t == null || !t.optBoolean("active", true)) continue;
                long at = WidgetTime.parse(EntityNames.text(t, "expires_at"));
                if (at <= 0 || (at < now - 60_000L && t.isNull("paused_at"))) continue;
                list.add(t);
            }
            java.util.Collections.sort(list, (a, b) -> Long.compare(
                    WidgetTime.parse(EntityNames.text(a, "expires_at")), WidgetTime.parse(EntityNames.text(b, "expires_at"))));
            TimeZone tz = TimeZone.getDefault();
            for (int i = 0; i < ROW.length; i++) {
                if (i >= list.size()) {
                    views.setViewVisibility(ROW[i], View.GONE);
                    continue;
                }
                JSONObject t = list.get(i);
                String type = EntityNames.text(t, "type");
                long at = WidgetTime.parse(EntityNames.text(t, "expires_at"));
                boolean timer = "timer".equals(type) || type.isEmpty();
                boolean paused = !t.isNull("paused_at") && !EntityNames.text(t, "paused_at").isEmpty();
                String emoji = EntityNames.text(t, "emoji");
                String title = EntityNames.text(t, "title");
                if (title.isEmpty()) title = timer ? "Timer" : "alarm".equals(type) ? "Alarm" : "Reminder";
                views.setViewVisibility(ROW[i], View.VISIBLE);
                st.tile(views, ROW[i], timer && !paused);
                views.setImageViewResource(ICON[i], timer ? R.drawable.mi_timer : "alarm".equals(type) ? R.drawable.mi_notifications : R.drawable.mi_event);
                st.icon(views, ICON[i], timer ? st.accent : st.second);
                views.setTextViewText(TITLE[i], emoji.isEmpty() ? title : emoji + " " + title);
                st.text(views, TITLE[i], st.text);
                st.text(views, SUB[i], st.muted);
                if (timer && !paused) {
                    int secs = t.optInt("duration_sec", 0);
                    views.setTextViewText(SUB[i], secs > 0 ? WidgetTime.minutes(Math.round(secs / 60f)) + " timer" : "Timer");
                    views.setViewVisibility(CLOCK[i], View.VISIBLE);
                    views.setViewVisibility(AT[i], View.GONE);
                    long base = android.os.SystemClock.elapsedRealtime() + (at - now);
                    views.setChronometer(CLOCK[i], base, null, true);
                    views.setChronometerCountDown(CLOCK[i], true);
                    views.setTextColor(CLOCK[i], st.text);
                } else {
                    views.setViewVisibility(CLOCK[i], View.GONE);
                    views.setViewVisibility(AT[i], View.VISIBLE);
                    if (paused) {
                        long left = Math.max(0, at - WidgetTime.parse(EntityNames.text(t, "paused_at")));
                        views.setTextViewText(AT[i], String.format(java.util.Locale.US, "%d:%02d", left / 60000, (left / 1000) % 60));
                        views.setTextViewText(SUB[i], "Paused");
                    } else {
                        views.setTextViewText(AT[i], WidgetTime.clock(at, tz));
                        views.setTextViewText(SUB[i], WidgetTime.day(at, now, tz));
                    }
                    st.text(views, AT[i], paused ? st.muted : st.text);
                }
            }
            if (list.isEmpty()) empty(views, "No timers or alarms running");
            else views.setViewVisibility(R.id.tm_empty, View.GONE);
        } catch (Exception e) {
            empty(views, WidgetApi.problem(e));
        }
        return views;
    }

    private static void empty(RemoteViews views, String message) {
        for (int id : ROW) views.setViewVisibility(id, View.GONE);
        views.setViewVisibility(R.id.tm_empty, View.VISIBLE);
        views.setTextViewText(R.id.tm_empty, message);
    }
}
