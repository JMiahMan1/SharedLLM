package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import org.json.JSONObject;

public class MediaWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushMedia(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushMedia(context);
        }
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_media);
        views.setOnClickPendingIntent(R.id.media_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.media_refresh, WidgetUpdater.refreshPendingIntent(context));
        views.setOnClickPendingIntent(R.id.media_prev, WidgetUpdater.mediaPendingIntent(context, "previous_track"));
        views.setOnClickPendingIntent(R.id.media_play, WidgetUpdater.mediaPendingIntent(context, "play_pause"));
        views.setOnClickPendingIntent(R.id.media_next, WidgetUpdater.mediaPendingIntent(context, "next_track"));

        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(R.id.media_title, "Sign in");
                views.setTextViewText(R.id.media_artist, "Open Jarvis OS");
                return views;
            }
            JSONObject status = WidgetApi.mediaStatus(context);
            JSONObject detail = status.optJSONObject("detail");
            if (detail == null) detail = status;
            JSONObject player = detail.optJSONObject("player");
            if (player == null) player = detail.optJSONObject("entity");
            String title = detail.optString("media_title", null);
            if (title == null || title.isEmpty()) title = detail.optString("title", null);
            String artist = detail.optString("media_artist", null);
            if (artist == null || artist.isEmpty()) artist = detail.optString("artist", null);
            String state = detail.optString("state", detail.optString("status", ""));
            if (title == null || title.isEmpty()) {
                if (player != null) {
                    title = player.optString("friendly_name", player.optString("entity_id", "Nothing playing"));
                    state = player.optString("state", state);
                    JSONObject pa = player.optJSONObject("attributes");
                    if (pa != null) {
                        String t = pa.optString("media_title", "");
                        if (!t.isEmpty()) title = t;
                        String a = pa.optString("media_artist", "");
                        if (!a.isEmpty()) artist = a;
                    }
                } else {
                    title = "Nothing playing";
                }
            }
            views.setTextViewText(R.id.media_title, DeviceButtonWidget.truncate(title, 28));
            views.setTextViewText(R.id.media_artist,
                DeviceButtonWidget.truncate(artist == null || artist.isEmpty() ? state : artist, 28));
            boolean playing = "playing".equalsIgnoreCase(state);
            views.setTextViewText(R.id.media_play, playing ? "⏸" : "▶");
        } catch (Exception e) {
            views.setTextViewText(R.id.media_title, "—");
            views.setTextViewText(R.id.media_artist, e.getMessage() != null ? e.getMessage() : "offline");
        }
        return views;
    }
}
