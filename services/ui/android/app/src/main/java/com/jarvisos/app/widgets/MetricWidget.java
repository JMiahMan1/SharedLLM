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
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.metric_root, R.id.metric_icon, R.id.metric_title, R.id.metric_refresh, st.accent);
        st.text(views, R.id.metric_value, st.text);
        st.text(views, R.id.metric_unit, st.muted);
        st.text(views, R.id.metric_label, st.text);
        st.text(views, R.id.metric_sub, st.muted);
        String entityId = WidgetApi.prefs(context).getString(PREF_ENTITY, "sensor.garage_door_internal_temperature");
        String label = WidgetApi.prefs(context).getString(PREF_LABEL, "Garage Temp");
        if (entityId == null || entityId.isEmpty()) entityId = "sensor.garage_door_internal_temperature";
        if (label == null || label.isEmpty()) label = "Metric";
        views.setTextViewText(R.id.metric_title, label.toUpperCase(java.util.Locale.ROOT));
        try {
            if (WidgetApi.apiKey(context) == null) {
                views.setTextViewText(R.id.metric_value, "-");
                st.status(views, R.id.metric_status, "Sign in to Jarvis OS");
                return views;
            }
            JSONObject states = WidgetApi.entityStates(context, java.util.Collections.singletonList(entityId));
            JSONObject e = states.optJSONObject(entityId);
            if (e == null) {
                views.setTextViewText(R.id.metric_value, "-");
                views.setTextViewText(R.id.metric_label, entityId);
                views.setTextViewText(R.id.metric_sub, "Not found in Home Assistant");
                return views;
            }
            String state = EntityNames.text(e, "state");
            JSONObject attrs = e.optJSONObject("attributes");
            String unit = EntityStates.field(e, attrs, "unit_of_measurement");
            String deviceClass = EntityStates.field(e, attrs, "device_class");
            views.setTextViewText(R.id.metric_value, EntityStates.number(state));
            views.setTextViewText(R.id.metric_unit, unit);
            views.setTextViewText(R.id.metric_label, WidgetApi.friendlyName(e));
            views.setTextViewText(R.id.metric_sub, EntityStates.updated(e));
            views.setImageViewResource(R.id.metric_icon, EntityStates.sensorIcon(deviceClass, unit));
            if ("unavailable".equalsIgnoreCase(state) || "unknown".equalsIgnoreCase(state)) {
                views.setTextViewText(R.id.metric_value, "-");
                views.setTextViewText(R.id.metric_sub, "Unavailable");
            }
        } catch (Exception ex) {
            views.setTextViewText(R.id.metric_value, "-");
            st.status(views, R.id.metric_status, ex.getMessage() != null ? ex.getMessage() : "Offline");
        }
        return views;
    }
}
