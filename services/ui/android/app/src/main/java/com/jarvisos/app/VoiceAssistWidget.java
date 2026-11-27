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
 * It sends a broadcast rather than opening the app, so the person never loses
 * what they were doing - the answer is spoken over it. This is the entry point
 * for "there should also be the option for an android homescreen button/widget
 * if a physical button can not be assigned".
 */
public final class VoiceAssistWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        for (int id : ids) {
            RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.voice_assist_widget);
            Intent talk = new Intent(context, AssistTriggerReceiver.class).setAction(AssistTriggerReceiver.ACTION_TALK);
            PendingIntent pending = PendingIntent.getBroadcast(context, id, talk,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            views.setOnClickPendingIntent(R.id.voice_assist_widget_root, pending);
            manager.updateAppWidget(id, views);
        }
    }
}
