package com.jarvisos.app;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;

/**
 * The home-screen button for talking to Jarvis: one tap starts a turn.
 *
 * It opens {@link AssistActivity} rather than sending a broadcast into
 * {@link AssistService}. Android 12+ refuses to let a background receiver start
 * a microphone foreground service, so the broadcast version died with
 * ForegroundServiceStartNotAllowedException and the widget did nothing. Opening
 * the activity puts the app in front, which is what makes the service legal.
 */
public final class VoiceAssistWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        for (int id : ids) {
            RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.voice_assist_widget);
            Intent talk = new Intent(context, AssistActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            PendingIntent pending = PendingIntent.getActivity(context, id, talk,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            views.setOnClickPendingIntent(R.id.voice_assist_widget_root, pending);
            manager.updateAppWidget(id, views);
        }
    }
}
