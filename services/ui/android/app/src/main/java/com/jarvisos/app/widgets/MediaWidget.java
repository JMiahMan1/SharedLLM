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
        views.setOnClickPendingIntent(R.id.media_root, WidgetUpdater.openPathPendingIntent(context, "/media", 7130));
        views.setOnClickPendingIntent(R.id.media_refresh, WidgetUpdater.refreshPendingIntent(context));
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.media_root, R.id.media_icon, R.id.media_header, R.id.media_refresh, st.accent);
        st.text(views, R.id.media_title, st.text);
        st.text(views, R.id.media_artist, st.muted);
        st.tile(views, R.id.media_art, true);
        st.icon(views, R.id.media_art, st.accent);
        st.fill(views, R.id.media_prev, R.drawable.wg_circle, st.tile);
        st.fill(views, R.id.media_next, R.drawable.wg_circle, st.tile);
        st.fill(views, R.id.media_play, R.drawable.wg_circle, st.accent);
        st.icon(views, R.id.media_prev, st.text);
        st.icon(views, R.id.media_next, st.text);
        st.icon(views, R.id.media_play, st.onAccent);
        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(R.id.media_title, "Sign in");
                views.setTextViewText(R.id.media_artist, "Open Jarvis OS");
                return views;
            }
            JSONObject status = WidgetApi.mediaStatus(context);
            MediaStatus.Lines lines = MediaStatus.fromJson(status);
            views.setTextViewText(R.id.media_title, lines.title);
            views.setTextViewText(R.id.media_artist, lines.subtitle);
            views.setImageViewResource(R.id.media_play, lines.playing ? R.drawable.mi_pause : R.drawable.mi_play);
            if (lines.playing) st.text(views, R.id.media_artist, st.accent);
            bindTransport(context, views, lines);
        } catch (Exception e) {
            views.setTextViewText(R.id.media_title, "Nothing playing");
            st.status(views, R.id.media_status, e.getMessage() != null ? e.getMessage() : "Offline");
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
