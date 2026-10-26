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

        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(R.id.media_title, "Sign in");
                views.setTextViewText(R.id.media_artist, "Open Jarvis OS");
                return views;
            }
            JSONObject status = WidgetApi.mediaStatus(context);
            MediaStatus.Lines lines = MediaStatus.fromJson(status);
            views.setTextViewText(R.id.media_title, DeviceButtonWidget.truncate(lines.title, 28));
            views.setTextViewText(R.id.media_artist, DeviceButtonWidget.truncate(lines.subtitle, 28));
            views.setTextViewText(R.id.media_play, lines.playing ? "⏸" : "▶");
            bindTransport(context, views, lines);
        } catch (Exception e) {
            views.setTextViewText(R.id.media_title, "-");
            views.setTextViewText(R.id.media_artist, e.getMessage() != null ? e.getMessage() : "offline");
            // Keep the buttons tappable but with no target; a press reports that
            // there is nothing active rather than silently doing nothing.
            bindTransport(context, views, MediaStatus.fromJson(null));
        }
        return views;
    }

    /**
     * Point the transport buttons at the player the widget is showing.
     *
     * <p>Every press carries the entity id and a command the execution route
     * accepts; without both, {@code /execute/media/transport} answers 422 and
     * the button is dead.
     */
    private static void bindTransport(Context context, RemoteViews views, MediaStatus.Lines lines) {
        views.setOnClickPendingIntent(R.id.media_prev, WidgetUpdater.mediaPendingIntent(
            context, MediaStatus.transportCommand("previous_track", lines.playing), lines.entityId));
        views.setOnClickPendingIntent(R.id.media_play, WidgetUpdater.mediaPendingIntent(
            context, MediaStatus.transportCommand("play_pause", lines.playing), lines.entityId));
        views.setOnClickPendingIntent(R.id.media_next, WidgetUpdater.mediaPendingIntent(
            context, MediaStatus.transportCommand("next_track", lines.playing), lines.entityId));
    }
}
