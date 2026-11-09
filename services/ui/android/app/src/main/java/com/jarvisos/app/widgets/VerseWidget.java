package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import org.json.JSONObject;

public class VerseWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushVerse(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushVerse(context);
        }
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_verse);
        views.setOnClickPendingIntent(R.id.verse_root, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=today", 7120));
        views.setOnClickPendingIntent(R.id.verse_refresh, WidgetUpdater.refreshPendingIntent(context));
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.verse_root, R.id.verse_icon, R.id.verse_title, R.id.verse_refresh, st.accent);
        st.text(views, R.id.verse_text, st.text);
        st.text(views, R.id.verse_reference, st.accent);
        st.text(views, R.id.verse_sub, st.muted);
        st.fill(views, R.id.verse_chip, R.drawable.wg_pill, com.jarvisos.app.JarvisTheme.withAlpha(st.fourth, 0.18f));
        st.icon(views, R.id.verse_chip_icon, st.fourth);
        st.text(views, R.id.verse_streak, st.text);
        views.setViewVisibility(R.id.verse_chip, android.view.View.GONE);
        if (WidgetApi.apiKey(context) == null) {
            views.setTextViewText(R.id.verse_text, "-");
            st.status(views, R.id.verse_status, "Sign in to Jarvis OS");
            return views;
        }
        VerseDaily.Lines lines;
        try {
            JSONObject daily = WidgetApi.get(context, "/api/bible/daily");
            lines = VerseDaily.fromJson(daily);
        } catch (Exception ex) {
            String message = ex.getMessage() != null ? ex.getMessage() : "offline";
            String sub = message.toLowerCase().contains("no bible text is imported")
                ? "No Bible text imported on the server"
                : VerseDaily.oneLine(message);
            lines = new VerseDaily.Lines("", "-", sub, "", message);
        }
        views.setTextViewText(R.id.verse_reference, lines.reference);
        views.setTextViewText(R.id.verse_text, lines.text);
        views.setTextViewText(R.id.verse_sub, lines.sub);
        // The streak rides in a chip: "6" with a flame, only while there is one.
        String n = lines.streak.replaceAll("[^0-9].*$", "");
        if (!n.isEmpty()) {
            views.setViewVisibility(R.id.verse_chip, android.view.View.VISIBLE);
            views.setTextViewText(R.id.verse_streak, n + (n.equals("1") ? " day" : " days"));
        }
        return views;
    }
}