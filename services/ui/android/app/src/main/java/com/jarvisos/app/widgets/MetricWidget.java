package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import org.json.JSONObject;

public class MetricWidget extends AppWidgetProvider {
    public static final String PREF_ENTITY = "metric_entity_id";
    public static final String PREF_LABEL = "metric_label";

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushMetric(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushMetric(context);
        }
    }


    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_metric);
        views.setOnClickPendingIntent(R.id.metric_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.metric_refresh, WidgetUpdater.refreshPendingIntent(context));

        String entityId = WidgetApi.prefs(context).getString(PREF_ENTITY, "sensor.garage_door_internal_temperature");
        String label = WidgetApi.prefs(context).getString(PREF_LABEL, "Garage Temp");
        if (entityId == null || entityId.isEmpty()) entityId = "sensor.garage_door_internal_temperature";
        if (label == null || label.isEmpty()) label = "Metric";
        views.setTextViewText(R.id.metric_label, label);

        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(R.id.metric_value, "—");
                views.setTextViewText(R.id.metric_sub, "Sign in to Jarvis OS");
                return views;
            }
            java.util.List<String> ids = java.util.Collections.singletonList(entityId);
            JSONObject states = WidgetApi.entityStates(context, ids);
            JSONObject e = states.optJSONObject(entityId);
            if (e == null) {
                views.setTextViewText(R.id.metric_value, "—");
                views.setTextViewText(R.id.metric_sub, entityId);
            } else {
                String state = e.optString("state", "?");
                JSONObject attrs = e.optJSONObject("attributes");
                String unit = attrs != null ? attrs.optString("unit_of_measurement", "") : "";
                views.setTextViewText(R.id.metric_value, state + (unit.isEmpty() ? "" : " " + unit));
                String friendly = WidgetApi.friendlyName(e);
                views.setTextViewText(R.id.metric_sub, friendly);
            }
        } catch (Exception ex) {
            views.setTextViewText(R.id.metric_value, "—");
            views.setTextViewText(R.id.metric_sub, ex.getMessage() != null ? ex.getMessage() : "offline");
        }
        return views;
    }
}
