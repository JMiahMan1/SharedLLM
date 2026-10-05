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
        views.setOnClickPendingIntent(R.id.verse_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.verse_refresh, WidgetUpdater.refreshPendingIntent(context));

        if (WidgetApi.apiKey(context) == null) {
            views.setTextViewText(R.id.verse_text, "—");
            views.setTextViewText(R.id.verse_sub, "Sign in to Jarvis OS");
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
            lines = new VerseDaily.Lines("", "—", sub, "", message);
        }

        views.setTextViewText(R.id.verse_reference, lines.reference);
        views.setTextViewText(R.id.verse_text, lines.text);
        views.setTextViewText(R.id.verse_streak, lines.streak);
        views.setTextViewText(R.id.verse_sub, lines.sub);
        return views;
    }
}